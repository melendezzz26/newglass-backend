from dataclasses import replace

from app.modules.optimization.domain.first_fit import FirstFitOptimizer
from app.modules.optimization.domain.problem import MaterialSource
from app.modules.optimization.infrastructure.comparison_snapshot import encode_result, decode_result, encode_problem, decode_problem
from app.modules.optimization.presentation.run_serialization import stock_response
from tests.unit.optimization.test_first_fit import config, piece, problem, stock


def test_remnant_code_is_frozen_in_result_and_transport():
    remnant = replace(stock(width=12, height=8), source=MaterialSource.REMNANT, codigo='RTZ-real-42')
    source_problem = problem([piece()], [remnant], config())
    restored = decode_result(encode_result(FirstFitOptimizer().optimize(source_problem)))
    assert restored.materials_used[0].codigo == 'RTZ-real-42'
    assert stock_response(restored.materials_used[0]).model_dump()['codigo'] == 'RTZ-real-42'
    assert decode_problem(encode_problem(source_problem)).groups[0].stock_candidates[0].codigo == 'RTZ-real-42'


def test_old_snapshot_and_sheet_keep_existing_identity():
    original = problem([piece()], [stock(width=12, height=8)], config())
    old = encode_problem(original)
    assert 'codigo' not in old['problem']['groups'][0]['stock_candidates'][0]
    restored = decode_problem(old).groups[0].stock_candidates[0]
    assert restored.codigo is None
    assert stock_response(restored).material_id == restored.material_id
