from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from app.models import (Base, Cliente, Configuracion, EjecucionOptimizacion, MetricaEjecucion,
                        Optimizacion, Pedido, Pieza, Plancha, Rol, TipoVidrio, TipoVidrioEspesor)
from app.modules.optimization.application.comparison_catalog import METHODS_V2
from app.modules.optimization.application.comparison_confirmation import ConfirmComparison
from app.modules.optimization.application.comparison_v2_contracts import ExecutionDiagnostic, ExecutionPayloadV2
from app.modules.optimization.application.errors import RunError
from app.modules.optimization.domain.metrics import ResidualPolicy, ResidualPolicyVersion, calculate_metrics
from app.modules.optimization.domain.problem import OptimizationResult, Placement
from app.modules.optimization.infrastructure.comparison_v2_snapshot import decode_result_v3, decode_result_v4
from app.modules.optimization.infrastructure.confirmation_adapter import SqlConfirmationRepository
from app.modules.optimization.infrastructure.run_snapshot import json_value
from app.modules.optimization.presentation.comparison_serialization import comparison_response, execution_response
from app.modules.optimization.presentation.comparison_v2_serialization import comparison_response_v2
from tests.integration.postgres_support import new_database
from . import test_comparison_persistence as v1
from .test_comparison_v2_confirmation import choose, confirm, inventory
from .test_comparison_v2_persistence import create, save, setup_v2  # noqa: F401
from .test_runs import prepared  # noqa: F401


@pytest.fixture
def session_factory(isolated_postgres):
    """Use the ORM schema in a disposable loopback database, with only test rows."""
    with new_database(isolated_postgres) as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=True)
        with factory.begin() as session:
            session.add_all((Rol(id_rol=3, nombre="Operario"), TipoVidrio(id_tipo_vidrio=7, nombre="QA", estado=True),
                Cliente(id_cliente=41, nombre_razon_social="QA", estado=True, fecha_registro=datetime.now(timezone.utc))))
            session.flush()
            session.add(TipoVidrioEspesor(id_tipo_vidrio=7, espesor_mm=6))
        yield factory


@pytest.fixture
def running_case(setup_v2, session_factory):
    order_id = setup_v2[1][0]
    with session_factory.begin() as session:
        configuration = session.scalar(select(Configuracion))
        configuration.ancho_min_retazo_mm = configuration.alto_min_retazo_mm = 10
        sheet = session.get(Plancha, 5)
        sheet.ancho_mm, sheet.alto_mm = 120, 10
        piece = session.scalar(select(Pieza).where(Pieza.id_pedido == order_id))
        piece.cantidad, piece.area_mm2 = 1, 990
        piece.geometria = dict(type="RECTANGULO", width_mm=110, height_mm=9)
        piece.dimensiones = dict(width_mm=110, height_mm=9)
    current = create(setup_v2, ids=[order_id])
    execution = current.executions[0]
    for state in ("STARTING", "RUNNING"):
        with setup_v2[2]() as tx:
            current = tx.comparisons_v2.set_dispatch_state(current.comparison_id, execution.execution_id, state, current.version)
    unit = current.problem.units[0]
    placement = Placement(piece=unit, stock=current.problem.groups[0].stock_candidates[0],
                          x_mm=D(10), y_mm=D(1), rotation_deg=D(0), geometry=unit.geometry)
    packed = OptimizationResult(problem=current.problem, placements=(placement,), computation_time_ms=1)
    return current, packed


def measured_payload(packed, version):
    measured, residuals = calculate_metrics(packed, D(10), D(10), policy=version)
    policy = ResidualPolicy(version=version, minimum_width_mm=D(10), minimum_height_mm=D(10))
    diagnostic = ExecutionDiagnostic(origin="ENGINE", termination_reason="COMPLETED", statistics={})
    return ExecutionPayloadV2(method="FF", result=measured, diagnostic=diagnostic, residual_policy=policy), residuals


@pytest.fixture
def selected_case(setup_v2, running_case):
    current, packed = running_case
    payload, residuals = measured_payload(packed, ResidualPolicyVersion.CORRECTED)
    with setup_v2[2]() as tx:
        current = tx.comparisons_v2.save_execution(current.comparison_id, current.executions[0].execution_id,
                                                  payload, residuals, current.version)
    for method in METHODS_V2[1:]:
        current = save(setup_v2, current, method, failed=True)
    with setup_v2[2]() as tx:
        current = tx.comparisons_v2.record_supervisor_stopped(current.comparison_id, datetime.now(timezone.utc), current.version)
        tx.session.get(Pedido, packed.problem.order_ids[0]).estado = "OPTIMIZADO"
    return choose(setup_v2, current, "FF"), packed


def stored_pattern(factory, execution_id):
    with factory() as session:
        return deepcopy(session.get(EjecucionOptimizacion, execution_id).patron_resultado)


@pytest.mark.parametrize("version", [None, ResidualPolicyVersion.LEGACY])
def test_new_execution_writer_requires_corrected_policy(version, running_case, setup_v2, session_factory):
    current, packed = running_case
    payload, residuals = measured_payload(packed, version or ResidualPolicyVersion.CORRECTED)
    if version is None:
        payload = replace(payload, residual_policy=None)
    identifier = current.executions[0].execution_id
    before = stored_pattern(session_factory, identifier)
    with pytest.raises(RunError, match="política de cálculo vigente"), setup_v2[2]() as tx:
        tx.comparisons_v2.save_execution(current.comparison_id, identifier, payload, residuals, current.version)
    assert stored_pattern(session_factory, identifier) == before


def install_historical_fixture(factory, execution_id, packed, *, accredited):
    legacy, residuals = measured_payload(packed, ResidualPolicyVersion.LEGACY)
    with factory.begin() as session:
        execution = session.get(EjecucionOptimizacion, execution_id)
        data = deepcopy(execution.patron_resultado)
        data["snapshot"]["result"] = json_value(legacy.result)
        data["residual_rectangles"] = json_value(residuals)
        if accredited:
            data["snapshot"]["residual_policy"] = json_value(legacy.residual_policy)
        else:
            data["snapshot"]["snapshot_version"] = 3
            del data["snapshot"]["residual_policy"]
            del data["frozen_input"]
        execution.patron_resultado = data
        metric = session.scalar(select(MetricaEjecucion).where(MetricaEjecucion.id_ejecucion == execution_id))
        metric.retazo_recuperable_mm2 = legacy.result.metrics.recoverable_remnant_mm2
        metric.merma_mm2 = legacy.result.metrics.waste_mm2


def test_corrected_result_round_trips_and_confirmation_preserves_its_calculation(selected_case, setup_v2, session_factory):
    selected, _ = selected_case
    identifier = selected.selected_execution_id
    stored = stored_pattern(session_factory, identifier)
    payload = decode_result_v4(stored["snapshot"], expected_method="FF")
    assert payload.result.metrics.recoverable_remnant_mm2 == 100
    assert payload.residual_policy == selected.executions[0].residual_policy
    assert stored["frozen_input"]["input_sha256"] == selected.input_sha256
    assert comparison_response_v2(selected).confirmable

    receipt = confirm(setup_v2, selected)
    assert [r.exact_area_mm2 for r in receipt.remnants_created] == [100]
    confirmed = stored_pattern(session_factory, identifier)
    assert confirmed.pop("confirmation")["execution_id"] == identifier
    assert confirmed == stored
    after = inventory(session_factory)
    assert after["sheets"] == [(5, 1, True)] and len(after["movements"]) == 1
    assert confirm(setup_v2, selected) == receipt
    assert inventory(session_factory) == after


def test_v3_is_readable_before_and_after_release_but_cannot_confirm(selected_case, setup_v2, session_factory):
    selected, packed = selected_case
    identifier = selected.selected_execution_id
    install_historical_fixture(session_factory, identifier, packed, accredited=False)
    stored = stored_pattern(session_factory, identifier)
    before = inventory(session_factory)
    with setup_v2[2]() as tx:
        historical = tx.comparisons_v2.get_owned(selected.comparison_id, setup_v2[0])
    payload = decode_result_v3(stored["snapshot"])
    assert historical.executions[0].result == payload.result
    assert historical.executions[0].residual_policy is None and payload.result.metrics.recoverable_remnant_mm2 == 0
    assert not comparison_response_v2(historical).confirmable
    with pytest.raises(RunError, match="no está acreditada") as failure:
        confirm(setup_v2, selected)
    assert failure.value.code == "NOT_CONFIRMABLE"
    assert inventory(session_factory) == before
    with setup_v2[2]() as tx:
        released = tx.comparisons_v2.release(selected.comparison_id, setup_v2[0], selected.version)
        assert released.released and released.executions[0].result == payload.result
    assert stored_pattern(session_factory, identifier) == stored
    assert inventory(session_factory)["movements"] == []


def test_explicit_legacy_calculation_and_existing_v3_receipt_are_preserved(selected_case, setup_v2, session_factory):
    selected, packed = selected_case
    identifier = selected.selected_execution_id
    install_historical_fixture(session_factory, identifier, packed, accredited=True)
    receipt = confirm(setup_v2, selected)
    assert receipt.remnants_created == ()
    after = inventory(session_factory)
    assert len(after["movements"]) == 1

    # Simulate an already-confirmed row written before policy versioning existed.
    install_historical_fixture(session_factory, identifier, packed, accredited=False)
    stored = stored_pattern(session_factory, identifier)
    with setup_v2[2]() as tx:
        historical = tx.comparisons_v2.get_owned(selected.comparison_id, setup_v2[0])
        assert historical.confirmation is not None
        assert historical.executions[0].result.metrics.recoverable_remnant_mm2 == 0
    assert confirm(setup_v2, selected) == receipt
    assert inventory(session_factory) == after and stored_pattern(session_factory, identifier) == stored


def test_failure_after_inventory_update_rolls_back_and_retry_is_idempotent(selected_case, setup_v2, session_factory, monkeypatch):
    selected, _ = selected_case
    before = inventory(session_factory)
    stored = stored_pattern(session_factory, selected.selected_execution_id)

    def fail_after_consume(*_):
        raise RuntimeError("Synthetic failure after consuming stock")

    with monkeypatch.context() as patch:
        patch.setattr(SqlConfirmationRepository, "record_movements", fail_after_consume)
        with pytest.raises(RuntimeError, match="Synthetic failure"):
            confirm(setup_v2, selected)
    assert inventory(session_factory) == before
    assert stored_pattern(session_factory, selected.selected_execution_id) == stored
    receipt = confirm(setup_v2, selected)
    assert confirm(setup_v2, selected) == receipt
    assert len(inventory(session_factory)["movements"]) == 1


def test_comparison_v1_keeps_result_and_public_contract(session_factory, prepared):
    from functools import partial
    from app.modules.optimization.infrastructure.comparison_uow import SqlComparisonUnitOfWork

    actor, ids, _ = prepared
    factory = partial(SqlComparisonUnitOfWork, session_factory)
    current = v1.accept((actor, ids[:1], factory))
    for method in v1.ENGINES:
        current = v1.save(factory, current, method)
    current = v1.finish(factory, current)
    with factory() as tx:
        current = tx.comparisons.select(current.comparison_id, current.executions[0].execution_id, current.version)
    identifier = current.selected_execution_id
    stored = stored_pattern(session_factory, identifier)
    assert stored["snapshot"]["snapshot_version"] == 2
    assert stored["residual_policy"]["version"] == ResidualPolicyVersion.CORRECTED
    summary = comparison_response(current).model_dump(mode="json")
    layout = execution_response(current, current.executions[0]).model_dump(mode="json")
    assert summary["confirmable"] and layout["result"]["metodo"] == "FF"
    assert "residual_policy" not in summary and "residual_policy" not in layout["result"]
    command = ConfirmComparison(factory)
    args = (current.comparison_id, identifier, current.version, "phase1b3-v1", actor)
    receipt = command.execute(*args)
    assert command.execute(*args) == receipt
    confirmed = stored_pattern(session_factory, identifier)
    assert confirmed.pop("confirmation")["execution_id"] == identifier and confirmed == stored


@pytest.mark.parametrize("mutation", ["missing-policy", "unknown-policy", "unknown-snapshot", "modified-minimum",
    "configuration-version", "input-hash", "owner-identity", "raster", "proposals", "metric", "missing-metrics",
    "sql-metric", "contour", "bounds"])
def test_discrepancies_stop_before_inventory_consumption(mutation, selected_case, setup_v2, session_factory, monkeypatch):
    selected, _ = selected_case
    identifier = selected.selected_execution_id
    with session_factory.begin() as session:
        execution = session.get(EjecucionOptimizacion, identifier)
        data = deepcopy(execution.patron_resultado)
        snapshot = data["snapshot"]
        if mutation == "missing-policy":
            del snapshot["residual_policy"]
        elif mutation == "unknown-policy":
            snapshot["residual_policy"]["version"] = "unrecognized-v99"
        elif mutation == "unknown-snapshot":
            snapshot["snapshot_version"] = 99
        elif mutation == "modified-minimum":
            snapshot["residual_policy"]["minimum_height_mm"] = "9"
        elif mutation == "configuration-version":
            row = session.get(Optimizacion, selected.comparison_id)
            row.comparison_snapshot = {**row.comparison_snapshot, "configuration_version": 2}
        elif mutation == "input-hash":
            data["frozen_input"]["input_sha256"] = "0" * 64
        elif mutation == "owner-identity":
            data["comparison_id"] = True
        elif mutation == "raster":
            snapshot["result"]["problem"]["config"]["resolution_mm"] = "2"
        elif mutation == "proposals":
            data["residual_rectangles"][0]["x_mm"] = "1"
        elif mutation == "metric":
            snapshot["result"]["metrics"]["waste_mm2"] = "111"
        elif mutation == "missing-metrics":
            snapshot["result"]["metrics"] = None
        elif mutation == "sql-metric":
            metric = session.scalar(select(MetricaEjecucion).where(MetricaEjecucion.id_ejecucion == identifier))
            metric.retazo_recuperable_mm2 = 99
        elif mutation == "contour":
            snapshot["result"]["placements"][0]["geometry"]["width_mm"] = 109
        elif mutation == "bounds":
            snapshot["result"]["placements"][0]["x_mm"] = "11"
        execution.patron_resultado = data
        flag_modified(execution, "patron_resultado")  # JSON true and integer 1 compare equal in Python.
    before = inventory(session_factory)
    monkeypatch.setattr(SqlConfirmationRepository, "consume", lambda *_: pytest.fail("Consumption was reached"))
    with pytest.raises(RunError) as failure:
        confirm(setup_v2, selected)
    assert failure.value.code == "NOT_CONFIRMABLE"
    assert inventory(session_factory) == before
