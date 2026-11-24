"""Run from repository root: python -m experiments.sprint2_quality.run --output DIR.

Each execution is a fresh process. Native engine limits are unchanged. Validation
occurs after the measured adapter returns. No database or HTTP calls.
"""
import argparse
import csv
from dataclasses import asdict
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

from app.modules.optimization.application.comparison_catalog import METHODS_V2
from app.modules.optimization.application.engine_execution import EngineExecutionAdapter, ExecutionScope
from app.modules.optimization.application.errors import TimeBudgetExceeded
from app.modules.optimization.domain.beam_search import BeamSearchLimits
from app.modules.optimization.domain.hole_filling import HoleFillingLimits
from app.modules.optimization.infrastructure.comparison_snapshot import problem_fingerprint
from app.modules.optimization.infrastructure.comparison_v2_snapshot import encode_result_v3
from app.modules.optimization.infrastructure.run_snapshot import json_value
from experiments.optimization.loader import build_problem
from .validate import validate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
POLICY = {"calculation_seconds": 120, "worker_address_bytes": 256 * 1024 * 1024,
          "process_timeout_seconds": 300, "warmups_per_case_engine": 1,
          "measured_repetitions": 5, "observation_callbacks": False}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def environment():
    cpu = next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                if line.startswith('model name')), platform.processor())
    return dict(system=platform.platform(), python=sys.version, cpu=cpu, visible_cpus=os.cpu_count(),
                memory=Path('/proc/meminfo').read_text(),
                dependencies=sorted(f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions()),
                backend_sha=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                policy=POLICY, beam_limits=asdict(BeamSearchLimits()), hole_limits=asdict(HoleFillingLimits()))


def measure(case_path, method, repetition):
    data = json.loads(Path(case_path).read_text())
    problem = build_problem(data)
    minimums = tuple(Decimal(str(data['residual_limits'][k]))
                     for k in ('minimum_width_mm', 'minimum_height_mm'))
    input_hash = digest({'problem_sha256': problem_fingerprint(problem),
                         'residual_limits': data['residual_limits'], 'policy': POLICY})
    resource.setrlimit(resource.RLIMIT_AS, (POLICY['worker_address_bytes'], POLICY['worker_address_bytes']))
    began = time.perf_counter()
    def checkpoint():
        if time.perf_counter() - began >= POLICY['calculation_seconds']:
            raise TimeBudgetExceeded()
    adapted = EngineExecutionAdapter(method).execute(
        ExecutionScope(comparison_id=1, execution_id=METHODS_V2.index(method)+1, method=method),
        problem, minimum_width_mm=minimums[0], minimum_height_mm=minimums[1], checkpoint=checkpoint)
    result, diagnostic = adapted.payload.result, adapted.payload.diagnostic
    reason = diagnostic.termination_reason
    status = ('COMPLETO' if result and result.complete else
              'TIMEOUT' if 'TIME' in reason else
              'RESOURCE_LIMIT' if any(s in reason for s in ('LIMIT', 'BUDGET')) else
              'PARCIAL' if result else 'ERROR')
    validation = validate(result, adapted.residuals, minimums) if result else {'valid': None}
    metrics = result.metrics if result else None
    row = dict(case_id=data['case_id'], input_sha256=input_hash, engine_code=method, repetition=repetition,
               status=status, termination_reason=reason, total_units=len(problem.units),
               placed_units=result.placed_units if result else None,
               coverage_pct=str(Decimal(result.placed_units)*100/len(problem.units)) if result else None,
               area_material_total_mm2=str(metrics.material_area_mm2) if metrics else None,
               area_piezas_colocadas_mm2=str(metrics.placed_piece_area_mm2) if metrics else None,
               retazo_recuperable_mm2=str(metrics.recoverable_remnant_mm2) if metrics else None,
               merma_mm2=str(metrics.waste_mm2) if metrics else None,
               merma_pct_derivada=str(metrics.waste_mm2*100/metrics.material_area_mm2) if metrics else None,
               aprovechamiento_pct=str(metrics.utilization_pct) if metrics else None,
               planchas_usadas=metrics.new_sheets_used if metrics else None,
               retazos_usados=sum(s.source.value=='RETAZO' for s in result.materials_used) if result else None,
               total_fuentes=len(result.materials_used) if result else None,
               computation_time_ms=result.computation_time_ms if result else None,
               valid=validation['valid'], problem_sha256=problem_fingerprint(problem))
    snapshot = encode_result_v3(adapted.payload)
    pattern = json_value(dict(placements=result.placements, metrics=result.metrics)) if result else None
    row['layout_sha256'] = digest(pattern) if pattern else None
    return dict(row=row, validation=validation, snapshot=snapshot,
                residuals=json_value(adapted.residuals), diagnostic=diagnostic.public())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cases', nargs='*')
    parser.add_argument('--repetitions', type=int, choices=[5], default=5)
    parser.add_argument('--worker', nargs=3, metavar=('CASE', 'METHOD', 'REPEAT'))
    parser.add_argument('--worker-output', type=Path)
    args = parser.parse_args()
    if args.worker:
        value = measure(args.worker[0], args.worker[1], int(args.worker[2]))
        args.worker_output.write_text(json.dumps(value, default=str, indent=2)+'\n')
        return
    if not args.output:
        parser.error('--output is required')
    args.output.mkdir(parents=True, exist_ok=True)
    env = environment()
    env_id = digest(env)
    manifest_path = args.output/'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        assert manifest['environment']['backend_sha'] == env['backend_sha'], 'Cannot mix commits'
        for field in ('system', 'python', 'cpu', 'visible_cpus', 'dependencies', 'policy', 'beam_limits', 'hole_limits'):
            assert manifest['environment'][field] == env[field], f'Cannot mix environments: {field}'
        env_id = manifest['environment_id']
    else:
        manifest = dict(environment_id=env_id, environment=env, started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                        repetitions=args.repetitions, files={})
        manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    paths = sorted((HERE/'cases').glob('*.json'))
    if args.cases:
        paths = [p for p in paths if p.stem in args.cases]
    for path in paths:
        for repeat in range(args.repetitions+1):
            # Rotate method order between repetitions; no overlapping workers.
            methods = METHODS_V2[repeat%6:] + METHODS_V2[:repeat%6]
            for method in methods:
                target = args.output/f'{path.stem}_{method}_{repeat}.json'
                if target.exists():
                    continue
                print(f'{path.stem} {method} repetition={repeat}'+(' WARMUP' if repeat==0 else ''), flush=True)
                try:
                    process = subprocess.run([sys.executable, '-m', 'experiments.sprint2_quality.run',
                        '--worker', str(path), method, str(repeat), '--worker-output', str(target)],
                        capture_output=True, text=True, timeout=POLICY['process_timeout_seconds'])
                    if process.returncode:
                        (args.output/f'{target.stem}.error.txt').write_text(process.stderr)
                        raise RuntimeError(f'Worker/validation failed: {target.name}; inspect error log')
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError(f'Process timeout (including validation): {target.name}') from error
    rows = []
    for path in sorted(args.output.glob('*_*.json')):
        value = json.loads(path.read_text())
        if 'row' in value and value['row']['repetition'] > 0:
            rows.append(dict(value['row'], environment=env_id, backend_sha=env['backend_sha']))
    if rows:
        with (args.output/'raw_results.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
    manifest['measured_executions'] = len(rows)
    manifest['warmups'] = len(list(args.output.glob('*_0.json')))
    manifest['files'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(args.output.iterdir()) if p.is_file() and p.name != 'manifest.json'}
    manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
    print(f'Finished: {len(rows)} measured executions', flush=True)


if __name__ == '__main__':
    main()
