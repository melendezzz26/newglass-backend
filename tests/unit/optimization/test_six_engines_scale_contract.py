"""Small, explicit contracts for the six-engine scale work.

The larger selector and pocket scenarios remain in the existing engine tests.
"""
from dataclasses import replace
from decimal import Decimal as D
import json
from pathlib import Path

import pytest

from app.modules.optimization.application.comparison_catalog import METHODS_V2
from app.modules.optimization.application.engine_execution import EngineExecutionAdapter, ExecutionScope
from experiments.sprint2_quality.validate import validate
from experiments.optimization.loader import build_problem
from tests.unit.optimization.test_first_fit import config, problem, stock
from tests.unit.optimization.test_preparation import piece
from tests.unit.optimization.test_worst_fit import candidate
from app.modules.optimization.domain.worst_fit import WorstFitSelector
from app.modules.optimization.domain.best_residual_fit import BestResidualFitSelector
from app.modules.optimization.domain.hole_filling import HoleFillingOptimizer


def execute(method, sample):
    return EngineExecutionAdapter(method).execute(
        ExecutionScope(comparison_id=1, execution_id=METHODS_V2.index(method) + 1, method=method),
        sample, minimum_width_mm=D(1), minimum_height_mm=D(1))


@pytest.mark.parametrize('method', METHODS_V2)
def test_known_trivial_capacity_is_complete_and_valid(method):
    sample = problem([piece(width=2, height=2, quantity=2)], [stock(width=6, height=4)])
    execution = execute(method, sample)
    assert execution.payload.diagnostic.termination_reason == 'COMPLETED'
    result = execution.payload.result
    assert result.complete and result.placed_units == result.total_units == 2
    assert validate(result, execution.residuals, (D(1), D(1)))['valid']


def test_ff_and_bf_make_distinct_source_decisions():
    sample = problem([piece(width=5, height=5)],
                     [stock(1, width=10, height=10), stock(2, width=6, height=10)])
    assert [(u.piece_id, u.unit_index) for u in sample.units] == [(1, 1)]
    ff, bf = (execute(method, sample).payload.result for method in ('FF', 'BF'))
    assert ff.complete and bf.complete
    assert (ff.placements[0].stock.material_id, bf.placements[0].stock.material_id) == (1, 2)


def test_beam_explores_alternatives_and_differs_from_ff_on_existing_cp01():
    path = Path(__file__).resolve().parents[3] / 'experiments/sprint2_quality/cases/CP-01.json'
    sample = build_problem(json.loads(path.read_text(encoding='utf-8')))
    ff = execute('FF', sample)
    beam = execute('M6-BS', sample)
    assert ff.payload.result.complete and beam.payload.result.complete
    assert beam.payload.diagnostic.statistics['expansions'] > len(sample.units)
    assert beam.payload.result.placements != ff.payload.result.placements
    repeat = execute('M6-BS', sample)
    assert repeat.payload.result.placements == beam.payload.result.placements
    for execution in (ff, beam):
        assert validate(execution.payload.result, execution.residuals, (D(1), D(1)))['valid']


def test_hf_real_pocket_statistics_and_final_decision():
    from tests.unit.optimization.test_hole_filling import product_problem, assert_pocket_connected_component_is_interior
    from app.modules.optimization.domain.first_fit import FirstFitOptimizer
    sample = product_problem()
    optimizer = HoleFillingOptimizer()
    result = optimizer.optimize(sample)
    baseline = FirstFitOptimizer().optimize(sample)
    stats = optimizer.stats
    assert stats.pocket_candidates > 0 and stats.anchors_checked > 0 and stats.accepted_pocket > 0
    assert not stats.coverage_fallback and not stats.sheet_fallback
    assert result.complete and result.placements != baseline.placements
    target = next(p for p in result.placements if p.piece.piece_id == 4)
    assert_pocket_connected_component_is_interior(result, target)
    print('HF_STATS', stats)


@pytest.mark.parametrize('mutation', ['overlap', 'separation', 'margin', 'outside', 'angle', 'area'])
def test_raster_oracle_rejects_explicit_corruptions(mutation):
    sample = problem([piece(width=2, height=2, quantity=2)], [stock(width=10, height=8)],
                     config(margin_mm=D(1), separation_mm=D(1)))
    execution = execute('FF', sample)
    result = execution.payload.result
    assert validate(result, execution.residuals, (D(1), D(1)))['valid']
    first, second = result.placements
    if mutation == 'overlap':
        second = replace(second, x_mm=first.x_mm, y_mm=first.y_mm)
    elif mutation == 'separation':
        second = replace(second, x_mm=first.x_mm + 2, y_mm=first.y_mm)
    elif mutation == 'margin':
        first = replace(first, x_mm=D(0))
    elif mutation == 'outside':
        first = replace(first, x_mm=D(10))
    elif mutation == 'angle':
        first = replace(first, rotation_deg=D(5))
    else:
        result = replace(result, metrics=replace(result.metrics, waste_mm2=result.metrics.waste_mm2 + 1))
    corrupted = replace(result, placements=(first, second))
    with pytest.raises(AssertionError):
        validate(corrupted, execution.residuals, (D(1), D(1)))


def test_exact_margin_and_separation_are_valid_on_grid():
    sample = problem([piece(width=2, height=2, quantity=2)], [stock(width=10, height=8)],
                     config(margin_mm=D(1), separation_mm=D(1)))
    execution = execute('FF', sample)
    first, second = execution.payload.result.placements
    assert (first.x_mm, first.y_mm, second.x_mm, second.y_mm) == (1, 1, 4, 1)
    assert validate(execution.payload.result, execution.residuals, (D(1), D(1)))['valid']


def test_rotation_must_match_oriented_geometry():
    sample = problem([piece(width=3, height=2)], [stock(width=6, height=4)])
    execution = execute('FF', sample)
    result = execution.payload.result
    corrupted = replace(result, placements=(replace(result.placements[0], rotation_deg=D(90)),))
    with pytest.raises(AssertionError):
        validate(corrupted, execution.residuals, (D(1), D(1)))


def test_result_contract_rejects_duplicate_and_unknown_source():
    result = execute('FF', problem([piece(width=2, height=2, quantity=2)], [stock(width=6, height=4)])).payload.result
    first, second = result.placements
    with pytest.raises(ValueError, match='distinct requested'):
        replace(result, placements=(first, first))
    unknown = replace(second, stock=stock(99, width=6, height=4))
    with pytest.raises(ValueError, match='stock must belong'):
        replace(result, placements=(first, unknown))


@pytest.mark.parametrize('field', ['placed_units', 'total_units'])
def test_validator_rejects_inconsistent_declared_counts(field):
    execution = execute('FF', problem([piece(width=2, height=2, quantity=2)], [stock(width=6, height=4)]))
    result = execution.payload.result
    class CorruptCounts:
        def __getattr__(self, name):
            return getattr(result, name) + 1 if name == field else getattr(result, name)
    with pytest.raises(AssertionError):
        validate(CorruptCounts(), execution.residuals, (D(1), D(1)))


def test_wf_selector_compares_two_used_feasible_sources():
    small = candidate(stock(1, width=4, height=4), row=0, column=1, used_at=(0, 0))
    large = candidate(stock(2, width=6, height=4), row=0, column=1, used_at=(0, 0), stock_rank=1)
    assert small.layout.check(small.orientation.mask, small.row, small.column).is_valid
    assert large.layout.check(large.orientation.mask, large.row, large.column).is_valid
    assert small.remaining_free_cells_after_placement < large.remaining_free_cells_after_placement
    assert WorstFitSelector().select(iter((small, large))) == large


def test_m5_selector_compares_residual_rectangle_on_two_used_sources():
    small = candidate(stock(1, width=4, height=4), row=0, column=1, used_at=(0, 0))
    # A second earlier placement makes this real used layout more fragmented.
    small = replace(small, layout=small.layout.place(small.orientation.mask, 2, 2))
    large = candidate(stock(2, width=6, height=4), row=0, column=1, used_at=(0, 0), stock_rank=1)
    selector = BestResidualFitSelector()
    assert small.layout.check(small.orientation.mask, small.row, small.column).is_valid
    assert large.layout.check(large.orientation.mask, large.row, large.column).is_valid
    assert selector._residual_score(large)[0] < selector._residual_score(small)[0]
    assert selector._residual_score(large)[1] < selector._residual_score(small)[1]
    assert selector.select(iter((small, large))) == large


@pytest.mark.parametrize('field,value', [('glass_type_id', 2), ('thickness_mm', D(8))])
def test_placement_contract_rejects_incompatible_material(field, value):
    result = execute('FF', problem([piece(width=2, height=2)], [stock(width=6, height=4)])).payload.result
    placement = result.placements[0]
    with pytest.raises(ValueError, match='incompatible materials'):
        replace(placement, stock=replace(placement.stock, **{field: value}))
