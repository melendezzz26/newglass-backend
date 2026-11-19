"""Durable scheduling for the existing V2 supervisor, within one API process."""
from datetime import datetime, timezone
import logging
from threading import Event, Thread

from fastapi import HTTPException

from app.shared.config import integer_value
from ..application.admission import OptimizerBusy
from ..application.comparison_acceptance_v2 import AcceptComparisonV2, SnapshotConflict
from ..application.comparison_v2_contracts import ExecutionDiagnostic, ExecutionPayloadV2
from ..application.errors import RunError
from .comparison_supervision_v2 import ComparisonV2Coordinator, SupervisedJob

logger = logging.getLogger(__name__)


def queue_limits():
    capacity = integer_value("NEWGLASS_COMPARISON_QUEUE_CAPACITY", default=8)
    maximum = integer_value("NEWGLASS_COMPARISON_MAX_ACTIVE", minimum=0, default=1)
    if capacity > 100 or maximum not in (0, 1):
        raise RuntimeError("La cola admite hasta 100 solicitudes y como máximo un supervisor activo.")
    return dict(queue_capacity=capacity, max_active=maximum)


def finish_waiting(tx, current, reason, *, statistics=None):
    if not current.waiting:
        raise RunError("STATE_CONFLICT", "La comparación ya tiene un supervisor o ha terminado.")
    diagnostic = ExecutionDiagnostic(origin="SUPERVISOR", termination_reason=reason, statistics=statistics or {})
    identifier = current.comparison_id
    for execution in current.executions:
        payload = ExecutionPayloadV2(method=execution.method, result=None, diagnostic=diagnostic)
        current = tx.comparisons_v2.save_execution(identifier, execution.execution_id, payload, (), current.version,
                                                   without_dispatch=True)
    tx.orders.transition(current.problem.order_ids, "EN_OPTIMIZACION", "PENDIENTE")
    return tx.comparisons_v2.record_supervisor_stopped(identifier, datetime.now(timezone.utc), current.version)


class SeparatedComparisonApi(ComparisonV2Coordinator):
    """API-side commands. Supervisors only exist in the standalone service."""

    external_worker = True

    def __init__(self, *args, queue_capacity, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue_capacity = queue_capacity

    def start_service(self):
        self.recover_pending(legacy_only=True)

    def start(self, order_ids, actor_id, key):
        try:
            return AcceptComparisonV2(self.uow, self.authorize).execute(
                order_ids, actor_id, key, queue_capacity=self.queue_capacity, external_worker=True)
        except OptimizerBusy as error:
            error.details = {**error.details, "request_accepted": False}
            raise

    def reconcile(self, identifier):
        with self.uow() as tx:
            return tx.worker_jobs.status(identifier) in ("RUNNING", "TERMINAL")

    def request_cancel(self, identifier, actor_id, key, expected_version):
        with self.uow() as tx:
            tx.comparisons_v2.lock_queue()
            self.authorize(tx, actor_id)
            current = tx.comparisons_v2.get_owned(identifier, actor_id)
            waiting = current.waiting
            if (not waiting and not current.terminal and current.cancellation is None
                    and tx.worker_jobs.status(identifier) not in ("RUNNING", "RECOVERY_BLOCKED")):
                raise RunError("RECOVERY_BLOCKED", "No se puede acreditar el propietario activo para cancelar.")
            current = tx.comparisons_v2.request_cancellation(identifier, actor_id, key, expected_version)
            return finish_waiting(tx, current, "CANCELLED") if waiting else current


class QueuedComparisonV2Coordinator(ComparisonV2Coordinator):
    def __init__(self, *args, queue_capacity=8, max_active=1, **kwargs):
        super().__init__(*args, **kwargs)
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= 100 or type(max_active) is not int or max_active not in (0, 1):
            raise ValueError("Invalid comparison queue limits")
        self.queue_capacity, self.max_active = queue_capacity, max_active
        self._stop_dispatch = Event()
        self._dispatcher = None

    def start(self, order_ids, actor_id, key):
        try:
            return AcceptComparisonV2(self.uow, self.authorize).execute(
                order_ids, actor_id, key, queue_capacity=self.queue_capacity)
        except OptimizerBusy as error:
            error.details = {**error.details, "request_accepted": False}
            raise

    def start_service(self):
        with self.lock:
            if self._dispatcher is not None and self._dispatcher.is_alive():
                return
            super().start_service()
            if self.max_active:
                self._stop_dispatch.clear()
                self._dispatcher = Thread(target=self._dispatch, name="comparison-v2-dispatch", daemon=True)
                self._dispatcher.start()

    def _dispatch(self):
        while not self._stop_dispatch.wait(.25):
            try:
                self._dispatch_once()
            except (OptimizerBusy, SnapshotConflict):
                continue
            except RunError as error:
                if error.code != "RECOVERY_BLOCKED":
                    logger.exception("V2 queue could not dispatch")
                self._stop_dispatch.wait(1)
            except Exception:
                logger.exception("V2 queue could not dispatch")
                self._stop_dispatch.wait(1)

    def _dispatch_once(self):
        with self.lock:
            if not self.max_active or self._stop_dispatch.is_set():
                return
            if self.job is not None and self.job.thread is not None and self.job.thread.is_alive():
                return
            # An idle poll never takes a slot away from legacy FF or V1.
            with self.uow() as tx:
                if not tx.comparisons_v2.has_waiting():
                    return
            self._retry_recovery()
            if self.job is not None and not self.job.closed.is_set():
                return
            lease = self.admission.acquire_comparison(0)
            job = None
            installed = False
            try:
                with self.uow() as tx:
                    current = tx.comparisons_v2.next_waiting()
                    if current is None:
                        return
                    if not current.waiting:
                        raise RunError("RECOVERY_BLOCKED", "La traza del primer trabajo en cola requiere revisión.")
                    try:
                        self.authorize(tx, current.actor_id)
                    except (RunError, HTTPException) as error:
                        if getattr(error, "code", None) != "FORBIDDEN" and getattr(error, "status_code", None) != 403:
                            raise
                        finish_waiting(tx, current, "EXECUTION_FAILED", statistics={"queue_permission_revoked": 1})
                        return
                    runner = self.runner_factory()
                    runner.preflight()
                    job = (self.job_factory or SupervisedJob)(current, runner, self.uow, lease, None)
                    if not tx.comparisons_v2.claim_supervisor(current.comparison_id, job.token, job.identity):
                        return
                previous = self.job
                self.job = job
                installed = True
                if previous is not None:
                    previous.dispose()
                if self._stop_dispatch.is_set():
                    self._close(job)
                    return
                self._launch(job)
            except BaseException:
                if installed:
                    job.cancel.set()
                    if job.thread is None or not job.thread.is_alive():
                        self._close(job)
                raise
            finally:
                if not installed:
                    lease.release()
                    if job is not None:
                        job.dispose()

    def request_cancel(self, identifier, actor_id, key, expected_version):
        with self.uow() as tx:
            tx.comparisons_v2.lock_queue()
            self.authorize(tx, actor_id)
            current = tx.comparisons_v2.get_owned(identifier, actor_id)
            if current.waiting:
                current = tx.comparisons_v2.request_cancellation(identifier, actor_id, key, expected_version)
                return finish_waiting(tx, current, "CANCELLED")
        return super().request_cancel(identifier, actor_id, key, expected_version)

    def shutdown(self):
        self._stop_dispatch.set()
        if self._dispatcher is not None:
            self._dispatcher.join(timeout=15)
        super().shutdown()
