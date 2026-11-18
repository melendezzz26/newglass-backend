"""Largest free rectangle is exact, deterministic, not a material AABB."""
from decimal import Decimal
from dataclasses import replace
import pytest
from app.modules.optimization.domain.metrics import largest_rectangle, calculate_metrics
from app.modules.optimization.domain.first_fit import FirstFitOptimizer
from app.modules.optimization.domain.problem import (OptimizationConfig, PieceUnit, MaterialGroup,
    OptimizationProblem, OptimizationResult, Placement, StockCandidate, MaterialSource)
from tests.unit.optimization.test_first_fit import config, problem, stock
from tests.unit.optimization.test_preparation import piece


def test_metrics_recovers_smaller_rectangle_that_meets_both_minimums():
    input_problem = problem([piece(width=110, height=9)], [stock(width=120, height=10)], config())
    unit = input_problem.units[0]
    placement = Placement(piece=unit, stock=input_problem.groups[0].stock_candidates[0],
                          x_mm=Decimal(10), y_mm=Decimal(1), rotation_deg=Decimal(0),
                          geometry=unit.geometry)
    packed = OptimizationResult(problem=input_problem, placements=(placement,))

    result, residuals = calculate_metrics(packed, Decimal(10), Decimal(10))

    assert [(r.x_mm, r.y_mm, r.width_mm, r.height_mm, r.area_mm2) for r in residuals] == [
        (0, 0, 10, 10, 100)
    ]
    assert result.metrics.recoverable_remnant_mm2 == 100
    assert result.metrics.waste_mm2 == 110
    assert result.placements == packed.placements


@pytest.mark.parametrize('min_width', range(1, 5))
@pytest.mark.parametrize('min_height', range(1, 5))
def test_largest_rectangle_exhaustive_3_by_3_matches_brute_force(min_width, min_height):
    for mask in range(512):
        rows = tuple((mask >> (3 * r)) & 7 for r in range(3))
        valid = []
        for row in range(3):
            for col in range(3):
                for w in range(min_width, 4 - col):
                    for h in range(min_height, 4 - row):
                        bits = ((1 << w) - 1) << col
                        if all(rows[r] & bits == bits for r in range(row, row + h)):
                            valid.append((row, col, w, h))
        expected = min(valid, key=lambda v: (-v[2]*v[3],v[0],v[1],-v[2],-v[3])) if valid else None
        assert largest_rectangle(rows, 3, min_width_cells=min_width, min_height_cells=min_height) == expected, rows


@pytest.mark.parametrize('rows,columns', [((), 0), ((), 3), ((0, 0), 0)])
def test_largest_rectangle_of_empty_raster(rows, columns):
    assert largest_rectangle(rows, columns, min_width_cells=2) is None


@pytest.mark.parametrize('resolution,minimum_cells', [('0.5', 20), ('1', 10), ('2', 5)])
def test_metrics_converts_minimum_dimensions_at_effective_resolution(monkeypatch, resolution, minimum_cells):
    from app.modules.optimization.domain import metrics

    calls = []

    def scan(free, columns, *, min_width_cells, min_height_cells):
        calls.append((min_width_cells, min_height_cells))
        return largest_rectangle(free, columns, min_width_cells=min_width_cells, min_height_cells=min_height_cells)

    monkeypatch.setattr(metrics, 'largest_rectangle', scan)
    cfg = config(resolution_mm=Decimal(resolution))
    input_problem = problem([piece(width=2, height=10)], [stock(width=12, height=10)], cfg)
    packed = FirstFitOptimizer().optimize(input_problem)

    result, residuals = calculate_metrics(packed, Decimal(10), Decimal(10))
    assert [(r.x_mm, r.y_mm, r.width_mm, r.height_mm) for r in residuals] == [(2, 0, 10, 10)]
    assert result.metrics.recoverable_remnant_mm2 == 100

    above_ten = Decimal('10.0000000000000000000000000001')
    for minimums in ((above_ten, Decimal(10)), (Decimal(10), above_ten)):
        result, residuals = calculate_metrics(packed, *minimums)
        assert residuals == ()
        assert result.metrics.recoverable_remnant_mm2 == 0
    assert calls == [(minimum_cells, minimum_cells), (minimum_cells + 1, minimum_cells),
                     (minimum_cells, minimum_cells + 1)]


@pytest.mark.parametrize('irregular,margin,separation', [(False,0,0), (False,1,1), (True,0,1)])
def test_metrics_residual_inside_real_free_geometry(irregular, margin, separation):
    cfg = OptimizationConfig(resolution_mm=1, margin_mm=margin, separation_mm=separation, angular_step_deg=90)
    unit = PieceUnit(order_id=1, piece_id=1, unit_index=1, glass_type_id=7, thickness_mm=6,
                     geometry={'type':'RECTANGULO','width_mm':2,'height_mm':2}, area_mm2=4)
    geometry = {'type':'POLIGONO_CONVEXO', 'vertices_mm':[[0,0],[8,0],[0,8]]} if irregular else {'type':'RECTANGULO','width_mm':8,'height_mm':8}
    stock = StockCandidate(source=MaterialSource.REMNANT, material_id=1, physical_unit_index=1,
                           glass_type_id=7, thickness_mm=6, geometry=geometry, area_mm2=32 if irregular else 64)
    problem = OptimizationProblem(order_ids=(1,), config=cfg,
        groups=(MaterialGroup(glass_type_id=7, thickness_mm=6, units=(unit,), stock_candidates=(stock,)),))
    placed = FirstFitOptimizer().optimize(problem)
    result, residuals = calculate_metrics(placed, Decimal(1), Decimal(1))
    assert len(residuals) == 1
    assert result.metrics.new_sheets_used == 0
    assert result.metrics.material_area_mm2 == stock.area_mm2
    assert result.metrics.placed_piece_area_mm2 == 4
    assert result.metrics.waste_mm2 + result.metrics.recoverable_remnant_mm2 + 4 == stock.area_mm2
    assert calculate_metrics(placed, Decimal(1), Decimal(1)) == (result, residuals)
    too_small, none = calculate_metrics(placed, Decimal(100), Decimal(100))
    assert none == () and too_small.metrics.recoverable_remnant_mm2 == 0


def test_timer_excludes_stock_and_delivery(monkeypatch):
    from app.modules.optimization.application.calculation import CalculateRun
    from app.modules.optimization.application.dto import OptimizationRun, RunStatus
    from contextlib import nullcontext
    from types import SimpleNamespace
    cfg = OptimizationConfig(resolution_mm=1, margin_mm=0, separation_mm=0, angular_step_deg=90)
    u = PieceUnit(order_id=1,piece_id=1,unit_index=1,glass_type_id=1,thickness_mm=1,
                  geometry={'type':'RECTANGULO','width_mm':1,'height_mm':1},area_mm2=1)
    problem = OptimizationProblem(order_ids=(1,), config=cfg, groups=(MaterialGroup(glass_type_id=1,thickness_mm=1,units=(u,)),))
    run = OptimizationRun(optimization_id=1,status=RunStatus.RUNNING,result=OptimizationResult(problem=problem,placements=()))
    runs = SimpleNamespace(get=lambda _:run,residual_limits=lambda _:(Decimal(1),Decimal(1)),save_result=lambda i,r,s:r)
    uow = lambda:nullcontext(SimpleNamespace(runs=runs))
    current = [0]
    class Provider:
        def list_compatible(self, key):
            current[0] += 5_000_000_000
            return ()
    def delivery(event): current[0] += 10_000_000_000
    def metrics(result, w, h):
        current[0] += 7_000_000
        return result, ()
    monkeypatch.setattr('app.modules.optimization.application.calculation.calculate_metrics', metrics)
    result = CalculateRun(uow,Provider(),clock=lambda:current[0]).execute(1,delivery)
    assert result.computation_time_ms == 7
