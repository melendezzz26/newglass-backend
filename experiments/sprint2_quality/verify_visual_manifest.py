"""Verify the selected reproduction evidence; does not run the benchmark."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VISUAL = ROOT / 'docs/evidence/sprint-02/backend-six-engines/visual'
manifest = json.loads((VISUAL / 'capture_manifest.json').read_text(encoding='utf-8'))
expected = {'EV-050': 50, 'EV-100': 100, 'EV-200': 200, 'EV-300': 300}
engines = {'FF', 'BF', 'WF', 'HF', 'M5', 'M6-BS'}
assert {case['case_id'] for case in manifest['cases']} == set(expected)
for case in manifest['cases']:
    total = expected[case['case_id']]
    assert case['total_units'] == case['api_requested_units'] == total
    assert set(case['placed_units_by_engine']) == engines
    assert all(value == total for value in case['placed_units_by_engine'].values())
for capture in manifest['captures']:
    assert capture['reproduction'] and not capture['historical_measurement']
    assert hashlib.sha256((VISUAL / capture['filename']).read_bytes()).hexdigest() == capture['sha256']
observation = VISUAL / manifest['current_independent_orders_observation']
assert hashlib.sha256(observation.read_bytes()).hexdigest() == manifest['current_independent_orders_observation_sha256']
evidence = json.loads(observation.read_text(encoding='utf-8'))
assert len({row['order_id'] for row in evidence['submissions']}) == 2
assert len({row['comparison_id'] for row in evidence['submissions']}) == 2
assert all(row['http_status'] == 201 for row in evidence['submissions'])
assert evidence['running_and_waiting_observed'] and evidence['automatic_advance_without_resubmission']
assert any(set(row['states'].values()) == {'RUNNING', 'WAITING'} for row in evidence['timeline'])
assert all(row['terminal'] and row['engines_complete'] == 6 for row in evidence['final'].values())
assert next(row for row in manifest['captures'] if row['filename'] == '08_Control_Concurrencia.png')['independent_orders'] is False
assert next(row for row in manifest['captures'] if row['filename'] == '08B_Concurrencia_Pedidos_Independientes.png')['independent_orders'] is True
print(f"PASS: {len(expected)} cases, {len(manifest['captures'])} capture hashes")
