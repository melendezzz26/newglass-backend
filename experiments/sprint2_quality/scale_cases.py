"""Deterministic scale fixtures with an independently constructed packing witness.

Units are 100 mm squares; 1000 mm sheets, 10 mm grid/margin/separation.
This intentionally simple workload measures scale, not a representative mix of
commercial shapes. Every sheet contains at most 64 witness pieces.
"""
from decimal import Decimal as D
from math import ceil

from app.modules.optimization.domain.metrics import calculate_metrics
from app.modules.optimization.domain.problem import OptimizationResult, Placement
from experiments.optimization.loader import build_problem
from experiments.sprint2_quality.validate import validate

SCALES = (50, 100, 200, 300)
MINIMUMS = (D(10), D(10))


def case_data(count):
    if count not in SCALES:
        raise ValueError('Unsupported scale')
    return dict(case_id=f'LOAD-{count:03d}', schema_version=1, rotation_allowed=True,
        order_ids=[1], config=dict(resolution_mm=10, angular_step_deg=5, margin_mm=10, separation_mm=10),
        residual_limits=dict(minimum_width_mm=10, minimum_height_mm=10),
        pieces=[dict(id_pedido=1, id_pieza=1, cantidad=count, id_tipo_vidrio=1,
                     espesor_mm=6, tipo_forma='RECTANGULO',
                     geometria=dict(type='RECTANGULO', width_mm=100, height_mm=100))],
        stock=[dict(material_id=1, quantity=ceil(count / 64), source='PLANCHA',
                    glass_type_id=1, thickness_mm=6,
                    geometry=dict(type='RECTANGULO', width_mm=1000, height_mm=1000))])


def witness(problem):
    placements = []
    for group in problem.groups:
        for index, unit in enumerate(group.units):
            source = group.stock_candidates[index // 64]
            slot = index % 64
            placements.append(Placement(piece=unit, stock=source,
                x_mm=D(10 + (slot % 8) * 110), y_mm=D(10 + (slot // 8) * 110),
                rotation_deg=D(0), geometry=unit.geometry))
    result = OptimizationResult(problem=problem, placements=tuple(placements))
    result, residuals = calculate_metrics(result, *MINIMUMS)
    assert result.complete
    validate(result, residuals, MINIMUMS)
    return result


def build_scale(count):
    data = case_data(count)
    problem = build_problem(data)
    if any(len(group.stock_candidates) > 50 for group in problem.groups):
        raise ValueError('Fixture exceeds product stock limit')
    witness(problem)
    return data, problem
