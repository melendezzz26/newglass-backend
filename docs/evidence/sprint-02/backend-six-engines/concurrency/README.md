# Local HTTP concurrency evidence

Four scenarios use real Uvicorn servers on loopback, an owned PostgreSQL 16
cluster and synthetic orders. The start barrier synchronizes HTTP requests.
R1 is one accepted six-engine comparison. R2a and R2b use two requests in one
server, with distinct and identical users respectively. R3 uses two servers and
two accepted jobs. There is one observation per scenario, not a distribution.

Linux `/proc` is sampled every 20 ms. Process trees include each web process and
its descendants, including workers and the multiprocessing resource tracker;
the client and the external temporary PostgreSQL process are excluded. CPU is
sampled user and system time since the measurement began and may undercount a
process that exits between observations. Summed RSS may double-count shared
pages; sampled maxima can miss peaks. Per-server peaks need not be simultaneous.
`job_observed_wall_ms` records when the client observed closure, relative to the
start of the scenario, not exact server-side finish times. G7 does not depict an
admission rejection as a second executed job.

`recovery_cancel_observations.json` records a separate run. Cross-process cancel
returned `RECOVERY_BLOCKED`; the owner completed. A second run killed only its
own synthetic server, after which the other server recovered a terminal failed
state with the original input hash. The test did not confirm or consume stock.
This is limited evidence of two specific cases, not a proof of multi-dyno safety.

Reproduce with the backend Python and local PostgreSQL 16 test binaries:

```sh
export PYTHON_DOTENV_DISABLED=1 NEWGLASS_TEST_PG_BIN=/usr/lib/postgresql/16/bin
export NEWGLASS_REQUIRE_POSTGRES=1 NEWGLASS_CONCURRENCY_EVIDENCE_DIR=/tmp/newglass-concurrency
python -m pytest tests/integration/optimization/test_scale_http_resources.py -q -s -p no:cacheprovider
python -m experiments.sprint2_quality.concurrency_report /tmp/newglass-concurrency
```

Generate charts in an environment that also has matplotlib. The test only writes
observations when `NEWGLASS_CONCURRENCY_EVIDENCE_DIR` is explicitly set.
