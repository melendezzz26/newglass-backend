"""Measurement integrity and rejection of corrupt results, not performance tests."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import json

import pytest

from app.modules.optimization.application.engine_execution import EngineExecutionAdapter, ExecutionScope
from experiments.optimization.loader import build_problem
from experiments.sprint2_quality.validate import validate
from experiments.sprint2_quality.report import summarize
from experiments.sprint2_quality.validate import interval_oracle
from tests.optimization.final_audit_support import largest_rectangle_oracle


def test_accelerated_oracle_exact_parity_with_original():
    import random
    generator=random.Random(25023)
    for _ in range(200):
        columns=generator.randrange(1,25)
        rows=[generator.randrange(1<<columns) for _ in range(generator.randrange(1,15))]
        assert interval_oracle(rows,columns)==largest_rectangle_oracle(rows,columns)
    for rows,columns in (([0]*12,9),([31]*12,5),([3,3,7,7,0,31],5)):
        assert interval_oracle(rows,columns)==largest_rectangle_oracle(rows,columns)


def execution():
    path=Path(__file__).resolve().parents[3]/'experiments/sprint2_quality/cases/EXP-SMALL-001.json'
    problem=build_problem(json.loads(path.read_text()))
    return EngineExecutionAdapter('FF').execute(ExecutionScope(comparison_id=1,execution_id=1,method='FF'),
        problem,minimum_width_mm=1,minimum_height_mm=1)


def test_oracle_accepts_real_result_and_rejects_false_area():
    adapted=execution(); result=adapted.payload.result
    assert validate(result,adapted.residuals,(Decimal(1),Decimal(1)))['valid']
    corrupt=replace(result,metrics=replace(result.metrics,waste_mm2=result.metrics.waste_mm2+1))
    with pytest.raises(AssertionError): validate(corrupt,adapted.residuals,(Decimal(1),Decimal(1)))


def test_oracle_rejects_outside_placement():
    adapted=execution(); result=adapted.payload.result
    corrupt=replace(result,placements=(replace(result.placements[0],x_mm=Decimal(1000)),*result.placements[1:]))
    with pytest.raises(AssertionError): validate(corrupt,adapted.residuals,(Decimal(1),Decimal(1)))


def test_summary_rejects_different_inputs_for_same_engine():
    with pytest.raises(AssertionError):
        summarize([dict(case_id='X',engine_code='FF',input_sha256='one'),
                   dict(case_id='X',engine_code='FF',input_sha256='two')])


@pytest.mark.parametrize('case_id', ['C-varied-rectangles', 'D-asymmetric-triangles', 'E-mixed-shapes'])
def test_full_validator_parity_on_geometry_and_controlled_corruption(case_id):
    from experiments.optimization.final_audit_cases import read_case
    from tests.optimization.final_audit_support import verify_geometry_and_metrics
    problem = build_problem(read_case(case_id))
    adapted = EngineExecutionAdapter('FF').execute(
        ExecutionScope(comparison_id=1, execution_id=1, method='FF'),
        problem, minimum_width_mm=1, minimum_height_mm=1)
    result = adapted.payload.result
    minimums = (Decimal(1), Decimal(1))
    verify_geometry_and_metrics(result, adapted.residuals, minimums)
    assert validate(result, adapted.residuals, minimums)['valid']
    assert len({p.stock.identity for p in result.placements}) > 1
    corrupt = replace(result, placements=(
        replace(result.placements[0], x_mm=Decimal(1000)), *result.placements[1:]))
    for verifier in (verify_geometry_and_metrics, validate):
        with pytest.raises(AssertionError):
            verifier(corrupt, adapted.residuals, minimums)
