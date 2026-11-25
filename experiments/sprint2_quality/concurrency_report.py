"""Plot one observed run per scenario, including rejected requests."""
import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    with (args.directory/'concurrency_raw_results.csv').open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert {row['scenario'] for row in rows} == {'R1', 'R2_distinct', 'R2_same', 'R3'}
    with (args.directory/'concurrency_summary.csv').open('w', newline='') as stream:
        columns = ['scenario', 'server_count', 'requests', 'accepted', 'rejected', 'rejection_code',
                   'wall_time_ms', 'cpu_user_s', 'cpu_system_s', 'cpu_total_s', 'peak_rss_mb',
                   'max_processes', 'final_status', 'snapshot_ok', 'persistence_ok', 'backend_sha', 'environment']
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore', lineterminator='\n')
        writer.writeheader(); writer.writerows(rows)
    labels = ['R1', 'R2a', 'R2b', 'R3']
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.4), layout='constrained')
    for ax, field, title in zip(axes, ('wall_time_ms', 'cpu_total_s', 'peak_rss_mb'),
                                ('Observed wall time (ms)', 'Sampled CPU (s)', 'Sampled aggregate RSS (MiB)')):
        values = [float(row[field]) for row in rows]
        bars = ax.bar(range(len(rows)), values, color=['#606b6d', '#a8a8a8', '#a8a8a8', '#465f70'])
        ax.set_title(title, fontsize=10)
        ax.set_xticks(range(len(rows)), labels, fontsize=7)
        ax.grid(axis='y', alpha=.2)
        ax.bar_label(bars, labels=[f'{value:.1f}' for value in values], fontsize=7, padding=2)
    fig.suptitle('One observation: R1 = 1 accepted; R2a/R2b = 1 accepted + 1 busy; R3 = 2 accepted', fontsize=10)
    fig.savefig(args.directory/'G7_concurrencia_recursos.png', dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    main()
