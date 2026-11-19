"""Standalone Linux service for the opt-in V2 worker pilot."""
import argparse
import logging
import resource
import signal
from functools import partial
from threading import Event
from time import monotonic, sleep

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.shared.config import database_engine_options, database_url
from .application.admission import OptimizerBusy
from .application.comparison_acceptance_v2 import SnapshotConflict
from .application.errors import RunError
from .infrastructure.comparison_v2_uow import SqlComparisonPersistenceUnitOfWork
from .infrastructure.in_process_admission import InProcessRunAdmission
from .infrastructure.worker_jobs import enable_pilot, require_pilot
from .infrastructure.worker_recovery import recover_abandoned_v2
from .presentation.comparison_queue_v2 import finish_waiting, queue_limits
from .presentation.comparison_supervision_v2 import ComparisonV2Coordinator, SupervisedJob

logger = logging.getLogger(__name__)


def cpu_ms():
    usage = [resource.getrusage(kind) for kind in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)]
    return round(1000 * sum(sample.ru_utime + sample.ru_stime for sample in usage))


class SeparatedComparisonWorker(ComparisonV2Coordinator):
    def __init__(self, unit_of_work, *, pool, max_active=1, **kwargs):
        super().__init__(unit_of_work, InProcessRunAdmission(), **kwargs)
        if max_active not in (0, 1):
            raise ValueError("Worker capacity must be 0 or 1")
        self.max_active, self.pool = max_active, pool

    def _claim(self):
        claimed = None
        try:
            with self.uow() as tx:
                claimed = self._claim_in_transaction(tx)
            return claimed
        except BaseException:
            if claimed is not None:
                claimed.lease.release()
                claimed.dispose()
            raise

    def _claim_in_transaction(self, tx):
        if not tx.comparisons_v2.lock_queue(wait=False):
            return None
        tx.session.execute(text("SET LOCAL lock_timeout = '250ms'"))
        tx.worker_jobs.block_stale()
        if recover_abandoned_v2(tx):
            return None  # Commit closure evidence before the next admission/claim transaction.
        row = tx.worker_jobs.next_waiting()
        if row is None:
            return None
        current = tx.comparisons_v2.get(row.comparison_id)
        if not current.waiting:
            raise RunError("RECOVERY_BLOCKED", "El registro operativo y la comparación difieren.")
        try:
            self.authorize(tx, current.actor_id)
        except Exception as error:
            if getattr(error, "code", None) != "FORBIDDEN" and getattr(error, "status_code", None) != 403:
                raise
            finish_waiting(tx, current, "EXECUTION_FAILED", statistics={"queue_permission_revoked": 1})
            return None
        runner = self.runner_factory()
        runner.preflight()
        lease = self.admission.acquire_comparison(0)
        job = None
        try:
            job = SupervisedJob(current, runner, self.uow, lease, None)
            job.claim = tx.worker_jobs.claim(row, job.token)
            if not tx.comparisons_v2.claim_supervisor(current.comparison_id, job.claim, job.identity):
                raise RunError("RECOVERY_BLOCKED", "No se pudo acreditar el reclamo comercial.")
            return job
        except BaseException:
            lease.release()
            if job is not None:
                job.dispose()
            raise

    def _start_claimed(self):
        job = self._claim()
        if job is None:
            return
        job.started_wall, job.started_cpu = monotonic(), cpu_ms()
        self.job = job
        try:
            self._launch(job, observe=False)
        except BaseException:
            job.cancel.set()
            self._close(job)
            raise

    def _observe_claim(self, job):
        if job.thread is not None and job.thread.is_alive():
            try:
                with self.uow() as tx:
                    tx.session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                    cancelled = tx.worker_jobs.heartbeat(job.comparison.comparison_id, job.claim)
            except SnapshotConflict:
                return  # The supervisor may be committing another result.
            except (RunError, SQLAlchemyError):
                job.cancel.set()
                raise
            if cancelled:
                job.cancel.set()
        elif self._close(job):
            report = job.runner.last_report or {}
            logger.info("worker_v2_closed comparison_id=%s wall_ms=%s cpu_ms=%s peak_rss_bytes=%s pool_limit=%s",
                        job.comparison.comparison_id, round((monotonic() - job.started_wall) * 1000),
                        cpu_ms() - job.started_cpu, report.get("maximum_aggregate_rss"), self.pool.size())
            job.dispose()
            self.job = None

    def serve(self, stop):
        with self.uow() as tx:
            require_pilot(tx.session)
        logger.info("worker_v2_ready max_active=%s pool_limit=%s", self.max_active, self.pool.size())
        stopping_at = None
        try:
            while True:
                if stop.is_set():
                    if self.job is None:
                        break
                    if stopping_at is None:
                        self.job.cancel.set()
                        stopping_at = monotonic() + 15
                    if monotonic() >= stopping_at:
                        break
                try:
                    if self.job is not None:
                        self._observe_claim(self.job)
                    elif self.max_active:
                        self._start_claimed()
                except (OptimizerBusy, SnapshotConflict):
                    pass
                except (RunError, SQLAlchemyError) as error:
                    reason = error.code if isinstance(error, RunError) else type(error).__name__
                    logger.warning("worker_v2_deferred reason=%s", reason)
                if stop.is_set():
                    # Pulse ownership during orderly cancellation until a verified close.
                    sleep(.25)
                else:
                    stop.wait(.25)
        finally:
            self.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument("--enable-pilot", action="store_true", help="Enable the worker profile on a drained database and exit")
    flags.add_argument("--enable-mixed", action="store_true", help="Enable distributed V1/V2 admission on a drained database")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    url = database_url()
    target = make_url(url)
    if target.get_backend_name() != "postgresql":
        parser.error("El worker requiere PostgreSQL.")
    if (args.enable_pilot or args.enable_mixed) and target.host not in ("127.0.0.1", "::1", "localhost"):
        parser.error("La activación del piloto requiere PostgreSQL local de loopback.")
    options = database_engine_options(url)
    options.setdefault("connect_args", {}).setdefault("connect_timeout", 3)
    engine = create_engine(url, **{**options, "pool_size": 2, "max_overflow": 0, "pool_timeout": 2})
    uow = partial(SqlComparisonPersistenceUnitOfWork, sessionmaker(engine))
    try:
        if args.enable_pilot or args.enable_mixed:
            with uow() as tx:
                adopted = enable_pilot(tx, mixed=args.enable_mixed)
            logger.info("worker_v2_pilot_enabled adopted=%s", adopted)
            return
        stop = Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stop.set())
        SeparatedComparisonWorker(uow, pool=engine.pool, max_active=queue_limits()["max_active"]).serve(stop)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
