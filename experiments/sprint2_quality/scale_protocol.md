# Sprint 2 - backend and six engines: scale protocol

This branch temporarily depends on PR #7. It extends its experiments; it does
not repeat the prior 270 measurements. Production algorithms are unchanged.

`LOAD-050`, `LOAD-100`, `LOAD-200`, and `LOAD-300` contain physical piece
units, not orders. The frozen generator `scale_cases.py` creates 100 x 100 mm
rectangles on 1000 x 1000 mm sheets, grid/margin/separation 10 mm and five
degree rotation. The same glass, 6 mm thickness, residual minima, ordered
snapshots and external policy apply to each method at a given load. This is a
deliberately simple, homogeneous synthetic workload, not a model of the full
commercial demand. It cannot establish a universal engine ranking.

At most 64 pieces are assigned to each witness sheet, so there are one, two,
four or five sheets respectively, below the product's 50-per-material limit.
The witness is constructed independently of the optimizers, passed through
production metrics and checked with the existing raster oracle before each
worker. It establishes feasibility for this input, not that every heuristic is
guaranteed to discover that solution. The oracle shares the canonical
rasterizer and is not an independent continuous geometry proof.

The pilot must contain one observation per load and method, matching input
hashes and successful validation before the warmup/measurement phases can run.
The warmup is separate; five measured fresh processes per combination are the
default. Method order rotates by repetition. The previous runner's 120-second
calculation checkpoint, 256 MiB RLIMIT_AS and 300-second whole-process guard
are preserved. RLIMIT_AS limits virtual address space, not RSS. Retain every
failure and classify it using its observed cause; `COMPLETE`, `PARTIAL`,
`TIMEOUT`, `RESOURCE_LIMIT` and `ERROR` are reporting categories.

`computation_time_ms` is the product timer. `wall_time_ms` is the parent clock
for the full child process. `cpu_user_s`, `cpu_system_s`, `cpu_total_s` and
`peak_rss_mb` cover the child process, including imports, construction and
verification of the witness, the engine and post-result validation. They are
therefore process-cost measurements, not isolated optimizer CPU/RSS. CPU
average is child CPU seconds divided by parent wall seconds times 100: 100%
means one fully occupied core. RSS is Linux `ru_maxrss`, not virtual memory;
it is not an aggregate of a web supervisor and workers. No p95 is justified by
five samples. Null fields on failed children remain null.

Commands, from the backend in the same isolated Linux environment:

```sh
export PYTHON_DOTENV_DISABLED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
python -m experiments.sprint2_quality.scale --phase pilot --output OUTPUT
python -m experiments.sprint2_quality.scale --phase warmup --output OUTPUT
python -m experiments.sprint2_quality.scale --phase measured --repetitions 5 --output OUTPUT
python -m experiments.sprint2_quality.scale_report OUTPUT
```

The raw JSON observations are written incrementally. Do not run phases in
parallel, combine operating systems or compare timings from different hosts.
Do not connect to production or confirm cuts. The workload is deliberately
coarse at a 10 mm raster; it does not establish practical capacity for 1 mm
commercial geometry, and a complete witness does not prove search optimality.
