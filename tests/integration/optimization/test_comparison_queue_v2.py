"""EN-CONC-001 on disposable PostgreSQL with explicit, local RBAC fixtures."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
import os
from threading import Barrier
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from app.models import (Base, Cliente, Configuracion, MovimientoInventario, Pedido, Permiso, Plancha, Retazo,
                        Rol, RolPermiso, TipoVidrio, TipoVidrioEspesor, Usuario)
from app.modules.authentication.presentation.dependencies import get_database_session
from app.modules.inventory.application.service import InventoryService
from app.modules.inventory.infrastructure.repository import SqlAlchemyInventoryRepository
from app.modules.inventory.presentation.dependencies import get_inventory_service
from app.modules.optimization.infrastructure.comparison_v2_uow import SqlComparisonPersistenceUnitOfWork
from app.modules.optimization.infrastructure.comparison_confirmation import SqlComparisonV2ConfirmationRepository
from app.modules.optimization.infrastructure.in_process_admission import InProcessRunAdmission
from app.modules.optimization.infrastructure.process_lifecycle import process_identity
from app.modules.optimization.infrastructure.sequential_runner import SequentialProcessRunner
from app.modules.optimization.presentation import comparison_supervision_v2
from app.modules.optimization.presentation.comparison_http_runtime_v2 import HttpComparisonJob
from app.modules.optimization.presentation.comparison_queue_v2 import QueuedComparisonV2Coordinator
from app.modules.orders.infrastructure.repositories import SQLAlchemyOrderRepository
from app.shared.security import create_access_token
from tests.integration.postgres_support import new_database
from tests.unit.optimization.test_sequential_runner import crash_hf, started_then_blocked

BASE = "/api/optimization/comparisons-v2"
ROLES = {"A": "Administrador", "O": "Operario", "L": "Almacenero"}
VIEW = {"optimization.view"}
OPERATE = VIEW | {"optimization.execute", "optimization.confirm"}
INVENTORY = {"inventory.view", "inventory.manage"}
PERMISSIONS = {"A": OPERATE | INVENTORY | {"users.manage"},
               "O": OPERATE | {"orders.view", "inventory.view"}, "L": VIEW | INVENTORY}


@pytest.fixture
def queue_api(isolated_postgres, monkeypatch):
    from main import app
    with new_database(isolated_postgres) as engine:
        assert engine.url.host == "127.0.0.1"
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine)
        now = datetime.now(timezone.utc)
        codes = {name: i for i, name in enumerate(sorted(set().union(*PERMISSIONS.values())), 1)}
        actors = {letter: i for i, letter in enumerate(ROLES, 1)}
        headers = {}
        with sessions.begin() as db:
            db.add_all(Rol(id_rol=actors[l], nombre=role) for l, role in ROLES.items())
            db.add_all(Permiso(id_permiso=i, codigo=name, modulo=name.split('.')[0], descripcion=name)
                       for name, i in codes.items())
            db.add_all([TipoVidrio(id_tipo_vidrio=7, nombre="QA"),
                        Cliente(id_cliente=1, nombre_razon_social="QA", estado=True, fecha_registro=now)])
            db.flush()
            for letter, actor in actors.items():
                dni = f"{actor:08}"
                username = f"{letter}{dni}"
                db.add(Usuario(id_usuario=actor, dni=dni, usuario=username, nombres="QA", apellidos=letter,
                               password_hash="unused-test-fixture", estado=True, fecha_creacion=now, id_rol=actor))
                db.add_all(RolPermiso(id_rol=actor, id_permiso=codes[name]) for name in PERMISSIONS[letter])
                headers[letter] = {"Authorization": "Bearer " + create_access_token(actor, username, ROLES[letter])}
            db.add(TipoVidrioEspesor(id_tipo_vidrio=7, espesor_mm=6))
            db.flush()
            db.add_all([Configuracion(version=1, separacion_mm=0, margen_mm=0, resolucion_raster_mm=1,
                paso_angular_grados=5, ancho_min_retazo_mm=1, alto_min_retazo_mm=1, vigente=True,
                id_usuario_creacion=actors['A'], fecha_creacion=now), Plancha(id_plancha=1, ancho_mm=5, alto_mm=5,
                espesor_mm=6, cantidad=1, estado=True, fecha_registro=now, id_tipo_vidrio=7)])
        with sessions() as db:
            repo = SQLAlchemyOrderRepository(db)
            piece = dict(tipo_forma="RECTANGULO", cantidad=1, dimensiones={"width_mm": 2, "height_mm": 2},
                         geometria={"type": "RECTANGULO", "width_mm": 2, "height_mm": 2}, id_tipo_vidrio=7,
                         espesor_mm=6, area_mm2=4)
            orders = {letter: repo.create_order(actor, [piece], id_cliente=1) for letter, actor in actors.items()}

        def new_coordinator(**options):
            return QueuedComparisonV2Coordinator(partial(SqlComparisonPersistenceUnitOfWork, sessions),
                InProcessRunAdmission(), job_factory=HttpComparisonJob, **options)

        api = SimpleNamespace(sessions=sessions, orders=orders, actors=actors, headers=headers, codes=codes,
                              queue=new_coordinator(max_active=0), new_coordinator=new_coordinator)

        def session():
            with sessions() as db:
                yield db

        monkeypatch.setitem(app.dependency_overrides, comparison_supervision_v2.get_comparison_v2_coordinator, lambda: api.queue)
        monkeypatch.setattr(comparison_supervision_v2, "get_comparison_v2_coordinator", lambda: api.queue)
        monkeypatch.setitem(app.dependency_overrides, get_database_session, session)
        monkeypatch.setitem(app.dependency_overrides, get_inventory_service,
                            lambda: InventoryService(SqlAlchemyInventoryRepository(sessions)))
        with TestClient(app) as client:
            api.client = client
            yield api


def post(api, actor, *, order=None, key="enconc-request-01"):
    return api.client.post(BASE, headers={**api.headers[actor], "Idempotency-Key": key},
                           json={"order_ids": [order or api.orders[actor]]})


def get(api, actor, comparison, suffix=""):
    return api.client.get(f"{BASE}/{comparison}{suffix}", headers=api.headers[actor])


def cancel(api, actor, summary):
    return api.client.post(f"{BASE}/{summary['comparison_id']}/cancel",
        headers={**api.headers[actor], "Idempotency-Key": "enconc-cancel-01"},
        json={"expected_version": summary["version"]})


def await_status(api, actor, comparison, state):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        response = get(api, actor, comparison, "/queue")
        assert response.status_code == 200, response.text
        if response.json()["state"] == state:
            return get(api, actor, comparison).json()
        time.sleep(.025)
    pytest.fail(f"Comparison {comparison} never reached {state}")


def inventory(api):
    with api.sessions() as db:
        return (db.get(Plancha, 1).cantidad, db.scalar(select(func.count()).select_from(Retazo)),
                db.scalar(select(func.count()).select_from(MovimientoInventario)))


def test_three_roles_queued_restart_active_cancel_and_other_jobs_complete(queue_api):
    api = queue_api
    for actor in ROLES:
        session = api.client.get("/api/auth/me", headers=api.headers[actor])
        assert session.status_code == 200
        assert set(session.json()["permisos"]) == PERMISSIONS[actor]
    assert api.client.get("/api/inventory/planchas", headers=api.headers["L"]).status_code == 200
    assert api.client.get("/api/inventory/planchas", headers=api.headers["O"]).status_code == 200
    assert api.client.post("/api/inventory/tipos-vidrio", headers=api.headers["L"],
                           json={"nombre": "QA adicional"}).status_code == 201
    assert api.client.post("/api/inventory/tipos-vidrio", headers=api.headers["O"],
                           json={"nombre": "QA denegado"}).status_code == 403
    assert post(api, "L").status_code == 403
    assert api.client.get(f"{BASE}/history", headers=api.headers["L"]).status_code == 200
    before = inventory(api)

    a, o = post(api, "A"), post(api, "O")
    assert a.status_code == o.status_code == 201
    first, second = a.json()["comparison_id"], o.json()["comparison_id"]
    assert first < second and post(api, "O").json()["comparison_id"] == second
    assert all(get(api, actor, identifier, "/queue").json()["state"] == "WAITING"
               for actor, identifier in (("A", first), ("O", second)))
    for actor, other in (("A", second), ("O", first), ("L", first)):
        assert get(api, actor, other, "/queue").status_code == 404
        assert get(api, actor, other).status_code == 404
    assert api.client.post(f"{BASE}/{first}/confirm", headers={**api.headers["L"], "Idempotency-Key": "not-authorized"},
        json={"execution_id": a.json()["executions"][0]["execution_id"], "expected_version": a.json()["version"]}).status_code == 403

    api.queue.shutdown()
    api.queue = api.new_coordinator(runner_factory=partial(SequentialProcessRunner, worker_target=started_then_blocked))
    api.queue.start_service()
    active = await_status(api, "A", first, "RUNNING")
    assert get(api, "O", second, "/queue").json()["state"] == "WAITING"
    deadline = time.monotonic() + 15
    while active["executions"][0]["dispatch_state"] != "RUNNING" and time.monotonic() < deadline:
        time.sleep(.025)
        active = get(api, "A", first).json()
    assert active["executions"][0]["dispatch_state"] == "RUNNING"
    extra = post(api, "O", order=api.orders["L"], key="cancel-in-queue").json()
    assert cancel(api, "O", extra).json()["cancel_state"] == "CANCELLED"
    assert get(api, "A", first, "/queue").json()["state"] == "RUNNING"
    api.queue.runner_factory = SequentialProcessRunner
    assert cancel(api, "O", active).status_code == 404
    assert cancel(api, "A", active).status_code in (200, 202)
    cancelled = await_status(api, "A", first, "TERMINAL")
    done = await_status(api, "O", second, "TERMINAL")
    assert cancelled["cancellation_status"] == "CANCELLED"
    assert all(row["complete"] for row in done["executions"])
    assert done["cancellation_status"] == "NONE" and inventory(api) == before


def test_idempotency_order_conflict_queue_capacity_and_queued_cancellation(queue_api):
    api = queue_api
    barrier = Barrier(2)

    def same_order(actor):
        barrier.wait(timeout=5)
        return post(api, actor, order=api.orders["A"])

    with ThreadPoolExecutor(2) as pool:
        attempts = dict(zip(("A", "O"), pool.map(same_order, ("A", "O"))))
    assert sorted(r.status_code for r in attempts.values()) == [201, 409]
    owner = next(actor for actor, response in attempts.items() if response.status_code == 201)
    winner = attempts[owner].json()
    assert post(api, owner, order=api.orders["A"]).json()["comparison_id"] == winner["comparison_id"]
    api.queue.queue_capacity = 1
    busy = post(api, "A", order=api.orders["O"], key="capacity-request")
    assert busy.status_code == 503 and busy.json()["detail"]["limit"]["name"] == "queued_comparisons"
    assert busy.headers["Retry-After"] and busy.json()["detail"]["request_accepted"] is False
    stopped = cancel(api, owner, winner)
    assert stopped.status_code == 200 and stopped.json()["cancel_state"] == "CANCELLED"
    assert cancel(api, owner, winner).json() == stopped.json()
    assert api.queue.job is None and api.queue.admission.active() == 0
    with api.sessions() as db:
        assert db.get(Pedido, api.orders["A"]).estado == "PENDIENTE"
    assert post(api, "O", order=api.orders["A"], key="fresh-request").status_code == 201
    assert inventory(api) == (1, 0, 0)


def test_revocation_in_queue_and_worker_failure_are_independent(queue_api):
    api = queue_api
    first = post(api, "A").json()["comparison_id"]
    second = post(api, "O").json()["comparison_id"]
    with api.sessions.begin() as db:
        db.execute(delete(RolPermiso).where(RolPermiso.id_rol == api.actors["A"],
                                          RolPermiso.id_permiso == api.codes["optimization.execute"]))
    api.queue.runner_factory = partial(SequentialProcessRunner, worker_target=crash_hf)
    api.queue.max_active = 1
    api.queue.start_service()
    denied = await_status(api, "A", first, "TERMINAL")
    assert all(e["diagnostic"]["statistics"] == {"queue_permission_revoked": 1} for e in denied["executions"])
    done = await_status(api, "O", second, "TERMINAL")
    assert [e["complete"] for e in done["executions"]] == [True, True, True, False, True, True]
    assert done["supervisor_stopped"] and inventory(api) == (1, 0, 0)


def test_parallel_retries_and_claims_keep_one_job_per_key_and_one_active(queue_api):
    api = queue_api
    barrier = Barrier(2)

    def retry(_):
        barrier.wait(timeout=5)
        result = post(api, "A")
        assert result.status_code == 201, result.text
        return result.json()["comparison_id"]

    with ThreadPoolExecutor(2) as pool:
        first, repeat = pool.map(retry, range(2))
    assert first == repeat
    second = post(api, "O").json()["comparison_id"]
    barrier = Barrier(2)

    def claim(_):
        barrier.wait(timeout=5)
        with api.queue.uow() as tx:
            current = tx.comparisons_v2.next_waiting()
            if current is None:
                return None
            assert tx.comparisons_v2.claim_supervisor(current.comparison_id, uuid4().hex, process_identity(os.getpid()))
            return current.comparison_id

    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(claim, range(2)))
    assert claimed.count(first) == 1 and claimed.count(None) == 1
    with api.queue.uow() as tx:
        assert tx.comparisons_v2.get(second).waiting and tx.comparisons_v2.next_waiting() is None


def test_simultaneous_admission_respects_the_last_queue_slot(queue_api):
    api = queue_api
    api.queue.queue_capacity = 1
    barrier = Barrier(2)

    def submit(actor):
        barrier.wait(timeout=5)
        return post(api, actor)

    with ThreadPoolExecutor(2) as pool:
        responses = dict(zip(("A", "O"), pool.map(submit, ("A", "O"))))
    assert sorted(response.status_code for response in responses.values()) == [201, 503]
    rejected = next(actor for actor, response in responses.items() if response.status_code == 503)
    with api.queue.uow() as tx:
        assert tx.comparisons_v2.waiting_count() == 1
    with api.sessions() as db:
        assert db.get(Pedido, api.orders[rejected]).estado == "PENDIENTE"


def test_concurrent_v4_confirmations_cannot_consume_shared_stock_twice(queue_api, monkeypatch):
    api = queue_api
    accepted = {actor: post(api, actor).json()["comparison_id"] for actor in ("A", "O")}
    api.queue.max_active = 1
    api.queue.start_service()
    selected = {}
    for actor, identifier in accepted.items():
        current = await_status(api, actor, identifier, "TERMINAL")
        response = api.client.put(f"{BASE}/{identifier}/selection", headers=api.headers[actor],
            json={"execution_id": current["executions"][0]["execution_id"], "expected_version": current["version"]})
        assert response.status_code == 200, response.text
        selected[actor] = response.json()
    assert inventory(api) == (1, 0, 0)
    barrier = Barrier(2)

    def confirm(actor):
        summary = selected[actor]
        return api.client.post(f"{BASE}/{accepted[actor]}/confirm",
            headers={**api.headers[actor], "Idempotency-Key": "enconc-confirm-01"},
            json={"execution_id": summary["selected_execution_id"], "expected_version": summary["version"]})

    def fail_on_receipt(*_):
        raise RuntimeError("controlled receipt failure")

    with monkeypatch.context() as patch:
        patch.setattr(SqlComparisonV2ConfirmationRepository, "mark_confirmed", fail_on_receipt)
        failed = confirm("A")
        assert failed.status_code == 500
        assert failed.json()["detail"]["code"] == "INTERNAL_ERROR"
    assert inventory(api) == (1, 0, 0)

    def simultaneous(actor):
        barrier.wait(timeout=5)
        return confirm(actor)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(simultaneous, ("A", "O")))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert next(r for r in results if r.status_code == 409).json()["detail"]["code"] == "STOCK_CONFLICT"
    assert inventory(api) == (0, 1, 1)
