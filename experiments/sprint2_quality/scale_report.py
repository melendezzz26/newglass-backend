"""Summarize measured scale rows, preserving failures and input identity."""
import argparse
import csv
from collections import Counter
from pathlib import Path
from statistics import median

from app.modules.optimization.application.comparison_catalog import METHODS_V2
from experiments.sprint2_quality.scale_cases import SCALES


def summarize(rows):
    summary = []
    for count in SCALES:
        load = [r for r in rows if int(r['load_size']) == count]
        hashes = {r['input_sha256'] for r in load if r.get('input_sha256')}
        if len(hashes) > 1:
            raise ValueError(f'Cannot compare different inputs: {count}')
        for method in METHODS_V2:
            group = [r for r in load if r['engine'] == method]
            if not group:
                raise ValueError(f'Missing {count} {method}')
            states = Counter(r['status'] for r in group)
            output = dict(load_size=count, engine=method, samples=len(group),
                          complete=states['COMPLETE'], partial=states['PARTIAL'],
                          timeout=states['TIMEOUT'], resource_limit=states['RESOURCE_LIMIT'],
                          error=states['ERROR'], input_sha256=next(iter(hashes), ''),
                          rows_without_input_hash=sum(not r.get('input_sha256') for r in group))
            for field in ('coverage_pct', 'waste_mm2', 'recoverable_mm2', 'utilization_pct',
                          'computation_time_ms', 'wall_time_ms', 'peak_rss_mb', 'cpu_total_s', 'avg_cpu_pct'):
                values = [float(r[field]) for r in group if r.get(field) not in (None, '')]
                output[field + '_median'] = median(values) if values else None
                if field in ('computation_time_ms', 'wall_time_ms'):
                    output[field + '_min'] = min(values) if values else None
                    output[field + '_max'] = max(values) if values else None
            summary.append(output)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    with (args.directory / 'measured_results.csv').open(newline='') as source:
        rows = list(csv.DictReader(source))
    summary = summarize(rows)
    with (args.directory / 'scale_summary.csv').open('w', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)


if __name__ == '__main__':
    main()
