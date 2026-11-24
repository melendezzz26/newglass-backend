"""Generate restrained scale charts from the measured summary only."""
import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from app.modules.optimization.application.comparison_catalog import METHODS_V2

COLORS = ('#252525', '#666666', '#999999', '#4c6a78', '#7c6655', '#706681')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    with (args.directory / 'scale_summary.csv').open(newline='') as source:
        rows = list(csv.DictReader(source))
    charts = [('G1_time', 'computation_time_ms_median', 'Median computation time (ms)'),
              ('G2_rss', 'peak_rss_mb_median', 'Median process peak RSS (MiB)'),
              ('G3_cpu', 'cpu_total_s_median', 'Median process CPU time (s)'),
              ('G4_waste', 'waste_mm2_median', 'Median waste A - P - R (mm²)'),
              ('G5_coverage', 'coverage_pct_median', 'Median coverage (%)')]
    for name, field, label in charts:
        fig, ax = plt.subplots(figsize=(8, 4.5), layout='constrained')
        for method, color in zip(METHODS_V2, COLORS):
            group = sorted((r for r in rows if r['engine'] == method), key=lambda r: int(r['load_size']))
            x = [int(r['load_size']) for r in group]
            y = [float(r[field]) if r[field] else float('nan') for r in group]
            ax.plot(x, y, marker='o', linewidth=1.5, color=color, label=method)
        ax.set(xlabel='Requested piece units', ylabel=label, xticks=[50, 100, 200, 300])
        ax.grid(alpha=.2)
        ax.legend(ncol=3, fontsize=8)
        fig.savefig(args.directory / f'{name}.png', dpi=160)
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 4.5), layout='constrained')
    ordered = sorted(rows, key=lambda r: (int(r['load_size']), METHODS_V2.index(r['engine'])))
    labels = [f"{r['load_size']}\n{r['engine']}" for r in ordered]
    bottom = [0] * len(ordered)
    for field, color in [('complete', '#626f70'), ('partial', '#b29b7d'),
                         ('timeout', '#9a726d'), ('resource_limit', '#7d738a'), ('error', '#313131')]:
        values = [int(r[field]) for r in ordered]
        ax.bar(range(len(rows)), values, bottom=bottom, color=color, label=field)
        bottom = [a+b for a, b in zip(bottom, values)]
    ax.set_xticks(range(len(labels)), labels, fontsize=6)
    ax.set_ylabel('Observations')
    ax.legend(ncol=5, fontsize=7)
    fig.savefig(args.directory / 'G6_status.png', dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 3.5), layout='constrained')
    matrix = [[next(int(r['complete']) / int(r['samples']) for r in rows
                    if r['engine'] == method and int(r['load_size']) == size)
               for size in (50, 100, 200, 300)] for method in METHODS_V2]
    ax.imshow(matrix, vmin=0, vmax=1, cmap='Greys', aspect='auto')
    ax.set_xticks(range(4), [50, 100, 200, 300])
    ax.set_yticks(range(6), METHODS_V2)
    ax.set_xlabel('Requested piece units')
    for row, values in enumerate(matrix):
        for col, value in enumerate(values):
            ax.text(col, row, f'{value:.0%}', ha='center', va='center',
                    color='white' if value > .5 else 'black')
    fig.savefig(args.directory / 'G8_capacity.png', dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    main()
