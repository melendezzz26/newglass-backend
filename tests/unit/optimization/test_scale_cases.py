import pytest

from app.modules.optimization.infrastructure.comparison_snapshot import problem_fingerprint
from experiments.sprint2_quality.scale_cases import SCALES, build_scale, case_data
from experiments.sprint2_quality.scale_report import summarize
from app.modules.optimization.application.comparison_catalog import METHODS_V2


@pytest.mark.parametrize('count', SCALES)
def test_scale_fixture_has_valid_complete_witness_and_product_stock_bound(count):
    data, problem = build_scale(count)
    assert data['case_id'] == f'LOAD-{count:03d}'
    assert len(problem.units) == count
    assert all(len(group.stock_candidates) <= 50 for group in problem.groups)
    assert case_data(count) == data
    assert problem_fingerprint(problem) == problem_fingerprint(build_scale(count)[1])


def test_summary_keeps_failures_and_rejects_mismatched_input():
    rows = [dict(load_size=str(count), engine=method, status='ERROR', input_sha256='x')
            for count in SCALES for method in METHODS_V2]
    result = summarize(rows)
    assert len(result) == 24 and all(row['error'] == 1 for row in result)
    rows[1]['input_sha256'] = 'different'
    with pytest.raises(ValueError, match='different inputs'):
        summarize(rows)
