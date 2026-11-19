"""Controlled real-spawn worker used ONLY by the isolated localhost E2E server.

Never imported by production modules or six production engines. The real
SequentialProcessRunner supervises and reaps this process exactly as in service.
"""
from contextlib import closing
from dataclasses import asdict
from functools import partial
import json
import os
from pathlib import Path
import time

from app.modules.optimization.application.engine_execution import ExecutionScope
from app.modules.optimization.infrastructure.comparison_snapshot import (
    decode_problem, problem_fingerprint,
)
from app.modules.optimization.infrastructure.process_lifecycle import (
    process_identity, protect_against_parent_exit,
)


def blocked_until_cancel(scope, payload, minimums, connection, gate, started_clock,
                         budget, checkpoint_cancel, parent_identity, observed):
    protect_against_parent_exit(parent_identity)
    owner = ExecutionScope(**scope)
    problem = decode_problem(json.loads(payload))
    identity = process_identity(os.getpid())
    def send(kind, data):
        connection.send_bytes(json.dumps(dict(scope=asdict(owner), kind=kind, data=data)).encode())
    try:
        send("ready", dict(identity=identity, input_sha256=problem_fingerprint(problem)))
        if not gate.wait(budget.startup_seconds):
            return
        started_clock.value = time.monotonic()
        send("started", dict(monotonic=started_clock.value))
        # The process remains real and alive until the runner signals its
        # cooperative checkpoint or performs verified termination + join.
        checkpoint_cancel.wait(60)
    finally:
        connection.close()


def gated_engine(*args):
    from app.modules.optimization.application.engine_execution import EngineExecutionAdapter
    from app.modules.optimization.infrastructure.sequential_worker import calculation_worker_v2

    if (args[0]["method"] == os.environ.get("QA_HOLD_METHOD", "FF")
            and int(os.environ["QA_HOLD_ORDER"]) in decode_problem(json.loads(args[1])).order_ids):
        original = EngineExecutionAdapter.execute
        resume = Path(os.environ["QA_RESUME_ENGINE"])

        def wait_then_execute(self, *values, **kwargs):
            while not resume.exists():
                kwargs["checkpoint"]()
                time.sleep(.025)
            return original(self, *values, **kwargs)

        EngineExecutionAdapter.execute = wait_then_execute
    calculation_worker_v2(*args)


def run_gated_worker():
    from app.modules.optimization import worker_v2
    from app.modules.optimization.infrastructure.sequential_runner import SequentialProcessRunner

    class GatedWorker(worker_v2.SeparatedComparisonWorker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, runner_factory=partial(SequentialProcessRunner, worker_target=gated_engine), **kwargs)

    worker_v2.SeparatedComparisonWorker = GatedWorker
    worker_v2.main()


def recovery_barrier():
    Path(os.environ["QA_RECOVERY_READY"]).touch()
    while not Path(os.environ["QA_RESUME_ENGINE"]).exists():
        time.sleep(.025)


def run_recovery_worker():
    from app.modules.optimization import worker_v2
    from app.modules.optimization.infrastructure.sequential_runner import SequentialProcessRunner

    class GatedRunner(SequentialProcessRunner):
        def events(self, *args, **kwargs):
            with closing(super().events(*args, **kwargs)) as events:
                for event in events:
                    yield event
                    if os.environ["QA_RECOVERY_PHASE"] == "AFTER" and event["kind"] == "result":
                        recovery_barrier()  # The result write and descendant join already occurred.

    class GatedWorker(worker_v2.SeparatedComparisonWorker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, runner_factory=GatedRunner, **kwargs)

        def _launch(self, *args, **kwargs):
            if os.environ["QA_RECOVERY_PHASE"] == "BEFORE":
                recovery_barrier()  # Claim committed. No spawn intent yet.
            return super()._launch(*args, **kwargs)

    worker_v2.SeparatedComparisonWorker = GatedWorker
    worker_v2.main()


def run_gated_v1_api(port):
    import uvicorn
    from main import app
    from app.modules.optimization.infrastructure.comparison_runner import ComparisonProcessRunner
    from app.modules.optimization.presentation.comparison_routes import get_comparison_runtime

    class GatedComparisonRunner(ComparisonProcessRunner):
        def calculate(self, *args, **kwargs):
            recovery_barrier()
            yield from super().calculate(*args, **kwargs)

    get_comparison_runtime().runner_factory = GatedComparisonRunner
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False)
