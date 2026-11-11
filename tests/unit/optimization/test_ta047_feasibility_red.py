"""Desired TA-047 rejection before accepting an infeasible comparison."""
import pytest
from decimal import Decimal as D

from app.modules.optimization.application.errors import RunError
from app.modules.optimization.application.geometric_preflight import validate_individual_feasibility
from tests.unit.optimization.test_first_fit import config, piece, problem, stock


@pytest.mark.parametrize('candidates, expected', [
    ([], 'NO_COMPATIBLE_STOCK'),
    ([stock(width=1, height=1)], 'PIECE_DOES_NOT_FIT'),
])
def test_infeasible_input_is_rejected_before_execution(candidates, expected):
    value = problem([piece(width=4, height=2)], candidates, config())
    with pytest.raises(RunError) as rejected:
        validate_individual_feasibility(value)
    assert rejected.value.code == expected


def test_rotation_can_make_a_piece_fit():
    value = problem([piece(width=3, height=1)], [stock(width=1, height=3)], config())
    validate_individual_feasibility(value)


def test_margin_can_make_a_piece_infeasible():
    value = problem([piece(width=3, height=3)], [stock(width=4, height=4)], config(margin_mm=D('1')))
    with pytest.raises(RunError) as rejected:
        validate_individual_feasibility(value)
    assert rejected.value.code == 'PIECE_DOES_NOT_FIT'


def test_individual_fit_does_not_claim_global_fit():
    value = problem([piece(width=2, height=2, quantity=2)], [stock(width=2, height=2)],
                    config(separation_mm=D('1')))
    validate_individual_feasibility(value)


def test_exhausted_search_is_not_reported_as_impossible():
    value = problem([piece(width=2, height=2)], [stock(width=2, height=2)], config())
    with pytest.raises(RunError) as rejected:
        validate_individual_feasibility(value, max_checks=0)
    assert rejected.value.code == 'RESOURCE_UNAVAILABLE'


def test_raster_resource_failure_is_not_reported_as_no_fit(monkeypatch):
    from app.modules.optimization.domain.rotation import RasterPreparationError
    def fail(*_args, **_kwargs):
        raise RasterPreparationError('cache limit')
    monkeypatch.setattr('app.modules.optimization.application.geometric_preflight.prepare_run_geometry', fail)
    with pytest.raises(RunError) as rejected:
        validate_individual_feasibility(problem([piece(width=2, height=2)], [stock(width=2, height=2)], config()))
    assert rejected.value.code == 'RESOURCE_UNAVAILABLE'


@pytest.mark.parametrize('shape', ['circle', 'polygon'])
def test_irregular_piece_larger_than_source_is_rejected(shape):
    row = piece(width=4, height=4)
    if shape == 'circle':
        row.update(tipo_forma='CIRCUNFERENCIA', geometria={'type': 'CIRCUNFERENCIA', 'radius_mm': 2}, area_mm2=D('12.5663706'))
    else:
        row.update(tipo_forma='POLIGONO_CONVEXO', geometria={'type': 'POLIGONO_CONVEXO', 'vertices_mm': [[0, 0], [4, 0], [0, 4]]}, area_mm2=D('8'))
    with pytest.raises(RunError) as rejected:
        validate_individual_feasibility(problem([row], [stock(width=1, height=1)], config()))
    assert rejected.value.code == 'PIECE_DOES_NOT_FIT'


def test_mixed_materials_use_compatible_sources():
    rows = [piece(identifier=1, glass=1, width=2, height=2), piece(identifier=2, glass=2, width=1, height=3)]
    candidates = [stock(identifier=1, glass=1, width=2, height=2), stock(identifier=2, glass=2, width=3, height=1)]
    validate_individual_feasibility(problem(rows, candidates, config()))
