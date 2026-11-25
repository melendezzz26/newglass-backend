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
print(f"PASS: {len(expected)} cases, {len(manifest['captures'])} capture hashes")
