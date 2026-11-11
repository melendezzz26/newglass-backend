"""Controlled real-spawn worker used ONLY by the isolated localhost E2E server.

Never imported by production modules or six production engines. The real
SequentialProcessRunner supervises and reaps this process exactly as in service.
"""
from dataclasses import asdict
import json
import os
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
