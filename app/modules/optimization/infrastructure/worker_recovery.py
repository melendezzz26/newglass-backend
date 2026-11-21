"""Recover V2 only where persisted evidence rules out a live engine tree."""
from datetime import datetime, timezone
import os

from app.models import OptimizationWorkerAttempt
from ..application.comparison_v2_contracts import ExecutionDiagnostic, ExecutionPayloadV2
from ..application.dto import RunStatus
from ..application.supervision_contracts import identity
from .process_lifecycle import process_identity, process_scope
from .worker_jobs import RecoveredClaim


def process_closed(recorded):
    actual = process_identity(recorded["pid"])
    if actual is not None:
        return actual != recorded
    # hidepid or permissions can hide a live /proc entry. Signal 0 sends no signal.
    try:
        os.kill(recorded["pid"], 0)
    except ProcessLookupError:
        return True
    return False


def proven_closure(current, attempt):
    """A PID check is comparable only in the recorded Linux boot and namespace."""
    owner = current.runtime.get("supervisor")
    if (current.terminal or not owner or attempt is None or attempt.closed_at is not None
            or owner["token"] != attempt.owner_token or dict(owner["identity"]) != attempt.owner_identity
            or current.runtime.get("owner_scope") != attempt.owner_scope):
        return None
    try:
        if attempt.owner_scope != process_scope() or not process_closed(identity(attempt.owner_identity)):
            return None
        verified = {str(e.execution_id) for e in current.executions if e.dispatch_state == "TERMINATED"
                    and e.diagnostic is not None and e.diagnostic.statistics.get("runtime_dispatched") == 1}
        workers = current.runtime["workers"]
        if (any(e.dispatch_state not in ("QUEUED", "TERMINATED") for e in current.executions)
                or workers.keys() != verified
                or any(worker is None or not process_closed(identity(dict(worker))) for worker in workers.values())):
            return None
    except (OSError, KeyError, ValueError, TypeError, IndexError):
        return None
    if not workers and all(e.dispatch_state == "QUEUED" for e in current.executions):
        return "NO_DISPATCH"
    if workers:
        return "DURABLE_JOIN"
    return None


def recover_abandoned_v2(tx):
    return any(_recover_v2(tx, job) for job in tx.worker_jobs.blocked("V2"))


def _recover_v2(tx, job):
    identifier = job.comparison_id
    current = tx.comparisons_v2.get(identifier)
    attempt = tx.session.get(OptimizationWorkerAttempt, (identifier, job.generation))
    proof = proven_closure(current, attempt)
    if proof is None:
        return False
    claim = RecoveredClaim(job.owner_token, job.generation)
    if proof == "NO_DISPATCH" and job.generation == 1 and current.cancellation is None:
        tx.comparisons_v2.reset_proven_unstarted(identifier, claim)
        return True
    _, current = tx.comparisons_v2.require_supervisor(identifier, claim)
    reason = "CANCELLED" if current.cancellation else "WORKER_STOPPED"
    for execution in current.executions:
        if execution.dispatch_state != "TERMINATED":
            failure = ExecutionPayloadV2(method=execution.method, result=None, diagnostic=ExecutionDiagnostic(
                origin="SUPERVISOR", termination_reason=reason, statistics={}))
            current = tx.comparisons_v2.save_execution(identifier, execution.execution_id, failure, (), current.version)
    cancelled = current.cancellation and any(e.diagnostic and e.diagnostic.termination_reason == "CANCELLED"
                                                for e in current.executions)
    tx.orders.transition(current.problem.order_ids, "EN_OPTIMIZACION",
                         "OPTIMIZADO" if not cancelled and any(e.eligible for e in current.executions) else "PENDIENTE")
    tx.comparisons_v2.record_supervisor_stopped(identifier, datetime.now(timezone.utc), current.version,
                                                claim=claim, closure=proof)
    return True


def recover_abandoned_ff(tx):
    return any(_recover_ff(tx, job) for job in tx.worker_jobs.blocked("FF"))


def _recover_ff(tx, job):
    identifier = job.comparison_id
    attempt = tx.session.get(OptimizationWorkerAttempt, (identifier, job.generation))
    if (attempt is None or attempt.owner_token != job.owner_token or attempt.closed_at is not None
            or attempt.dispatch_at is not None or attempt.worker_identity is not None):
        return False  # A dispatch intent with no durable join is ambiguous, even if the recorded child vanished.
    try:
        if attempt.owner_scope != process_scope() or not process_closed(identity(attempt.owner_identity)):
            return False
    except (OSError, KeyError, ValueError, TypeError):
        return False
    run, _, _ = tx.runs.ff_worker_input(identifier)
    if run.status is not RunStatus.RUNNING:
        return False
    tx.orders.require_state(run.result.problem.order_ids, "EN_OPTIMIZACION")
    claim = RecoveredClaim(job.owner_token, job.generation)
    if job.generation == 1:
        tx.worker_jobs.requeue_proven(identifier, claim)
    else:
        tx.runs.fail(identifier, "WORKER_STOPPED", claim=claim)
        tx.worker_jobs.finish_if_registered(identifier, claim, closure="NO_DISPATCH")
    return True
