"""Isolated six-engine scale pilot and benchmark; Linux only.

Run pilot first: python -m experiments.sprint2_quality.scale --output DIR --phase pilot
Then run warmup and measured phases separately, preserving each CSV.
"""
import argparse
import csv
from decimal import Decimal
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

from app.modules.optimization.application.comparison_catalog import METHODS_V2
from app.modules.optimization.application.engine_execution import EngineExecutionAdapter, ExecutionScope
from app.modules.optimization.application.errors import TimeBudgetExceeded
from app.modules.optimization.infrastructure.comparison_snapshot import problem_fingerprint
from experiments.sprint2_quality.run import POLICY, digest, environment
from experiments.sprint2_quality.scale_cases import SCALES, MINIMUMS, build_scale
from experiments.sprint2_quality.validate import validate


def worker(count, method):
    data, problem = build_scale(count)
    problem_hash = problem_fingerprint(problem)
    input_hash = digest(dict(problem_sha256=problem_hash, residual_limits=data['residual_limits'], policy=POLICY))
    limit = POLICY['worker_address_bytes']
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    start = time.monotonic()
    def checkpoint():
        if time.monotonic() - start >= POLICY['calculation_seconds']:
            raise TimeBudgetExceeded()
    execution = EngineExecutionAdapter(method).execute(
        ExecutionScope(comparison_id=1, execution_id=METHODS_V2.index(method)+1, method=method),
        problem, minimum_width_mm=MINIMUMS[0], minimum_height_mm=MINIMUMS[1], checkpoint=checkpoint)
    result, diagnostic = execution.payload.result, execution.payload.diagnostic
    reason = diagnostic.termination_reason
    status = ('COMPLETE' if result and result.complete else
              'TIMEOUT' if 'TIME' in reason else
              'RESOURCE_LIMIT' if 'LIMIT' in reason or 'MEMORY' in reason else
              'PARTIAL' if result else 'ERROR')
    valid = validate(result, execution.residuals, MINIMUMS)['valid'] if result else None
    m = result.metrics if result else None
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return dict(load_size=count, engine=method, total_units=len(problem.units),
        placed_units=result.placed_units if result else None,
        coverage_pct=float(Decimal(result.placed_units)*100/len(problem.units)) if result else None,
        status=status, termination_reason=reason,
        area_material_total_mm2=str(m.material_area_mm2) if m else None,
        area_piezas_colocadas_mm2=str(m.placed_piece_area_mm2) if m else None,
        recoverable_mm2=str(m.recoverable_remnant_mm2) if m else None,
        waste_mm2=str(m.waste_mm2) if m else None,
        utilization_pct=str(m.utilization_pct) if m else None,
        sheets_used=m.new_sheets_used if m else None,
        remnants_used=sum(s.source.value == 'RETAZO' for s in result.materials_used) if result else None,
        sources_used=len(result.materials_used) if result else None,
        computation_time_ms=result.computation_time_ms if result else None,
        peak_rss_mb=usage.ru_maxrss/1024, cpu_user_s=usage.ru_utime,
        cpu_system_s=usage.ru_stime, cpu_total_s=usage.ru_utime+usage.ru_stime,
        validation_pass=valid, input_sha256=input_hash, problem_sha256=problem_hash,
        diagnostic_statistics=dict(diagnostic.statistics))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    parser.add_argument('--phase', choices=('pilot', 'warmup', 'measured'))
    parser.add_argument('--repetitions', type=int, choices=(3, 5), default=5)
    parser.add_argument('--worker', nargs=2, metavar=('COUNT', 'METHOD'))
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(int(args.worker[0]), args.worker[1]), sort_keys=True))
        return
    if not args.output or not args.phase:
        parser.error('--output and --phase are required')
    args.output.mkdir(parents=True, exist_ok=True)
    if args.phase != 'pilot':
        pilot_path = args.output / 'pilot_observations.json'
        if not pilot_path.exists():
            parser.error('a completed pilot is required before warmup or measurement')
        pilot = json.loads(pilot_path.read_text())
        if len(pilot) != len(SCALES) * len(METHODS_V2):
            parser.error('pilot must contain all 24 scale/engine observations')
        for count in SCALES:
            group = [r for r in pilot if r['load_size'] == count]
            if (len(group) != len(METHODS_V2) or {r['engine'] for r in group} != set(METHODS_V2)
                or len({r.get('input_sha256') for r in group}) != 1
                or any(not r.get('input_sha256') or r.get('validation_pass') is not True for r in group)):
                parser.error(f'pilot inputs or validation failed for LOAD-{count:03d}')
    if args.phase == 'measured' and not (args.output / 'warmup_results.csv').exists():
        parser.error('completed warmups are required before measurement')
    env = environment()
    env_id = digest(env)
    rows = []
    for count in SCALES:
        for repeat in range(1, (args.repetitions if args.phase == 'measured' else 1)+1):
            methods = METHODS_V2[(repeat-1)%6:] + METHODS_V2[:(repeat-1)%6]
            for method in methods:
                print(f'{args.phase} LOAD-{count:03d} {method} {repeat}', flush=True)
                start = time.monotonic()
                try:
                    proc = subprocess.run([sys.executable, '-m', 'experiments.sprint2_quality.scale', '--worker', str(count), method],
                                          capture_output=True, text=True, timeout=POLICY['process_timeout_seconds'])
                    wall = (time.monotonic()-start)*1000
                    if proc.returncode:
                        row = dict(load_size=count, engine=method, status='ERROR', termination_reason='WORKER_STOPPED',
                                   error=proc.stderr[-2000:])
                    else:
                        row = json.loads(proc.stdout)
                except subprocess.TimeoutExpired:
                    wall = (time.monotonic()-start)*1000
                    row = dict(load_size=count, engine=method, status='TIMEOUT', termination_reason='SUPERVISOR_TIMEOUT')
                row.update(phase=args.phase, repetition=repeat, wall_time_ms=round(wall, 3),
                           backend_sha=env['backend_sha'], environment=env_id)
                if row.get('cpu_total_s') is not None:
                    row['avg_cpu_pct'] = 100 * row['cpu_total_s'] / (wall / 1000)
                rows.append(row)
                # Keep each observation even if a later worker fails or is interrupted.
                (args.output/f'{args.phase}_observations.json').write_text(json.dumps(rows, indent=2)+'\n')
    keys = sorted(set().union(*(r.keys() for r in rows)))
    with (args.output/f'{args.phase}_results.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    (args.output/f'{args.phase}_manifest.json').write_text(json.dumps(dict(environment=env,
        environment_id=env_id, repetitions=args.repetitions if args.phase == 'measured' else 1,
        phase=args.phase, note='CPU and RSS include worker startup, witness and validation; wall includes process startup.'),
        indent=2)+'\n')


if __name__ == '__main__':
    main()
