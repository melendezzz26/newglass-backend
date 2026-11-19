"""Run standalone FF under the same Linux process supervisor as V2."""
from contextlib import closing
from dataclasses import dataclass, field
from decimal import Decimal
import logging
from threading import Event, Thread
from time import monotonic, sleep

from sqlalchemy.exc import OperationalError

from ..application.comparison_acceptance_v2 import SnapshotConflict
from ..application.engine_execution import ExecutionScope
from ..application.errors import WorkersNotStopped
from ..domain.problem import OptimizationProblem
from .sequential_runner import SequentialProcessRunner
from .worker_jobs import WorkerClaim

logger = logging.getLogger(__name__)


@dataclass
class DurableFFWork:
    scope: ExecutionScope
    problem: OptimizationProblem
    minimums: tuple[Decimal, Decimal]
    claim: WorkerClaim
    runner: SequentialProcessRunner
    cancel: Event = field(default_factory=Event)
    thread: Thread | None = None
    started_wall: float = 0
    started_cpu: int = 0

    @property
    def identifier(self):
        return self.scope.comparison_id

    def execute(self, uow):
        terminal = None
        try:
            with closing(self.runner.standalone_ff(self.scope, self.problem, self.minimums,
                                                   cancel=self.cancel)) as stream:
                for packet in stream:
                    kind, data = packet["kind"], packet["data"]
                    if kind == "result":
                        terminal = data
                    elif kind in ("dispatching", "spawned"):
                        with uow() as tx:
                            # The intent commits before Process.start, and the child waits at its gate.
                            tx.worker_jobs.record_ff_dispatch(self.identifier, self.claim,
                                                              process=data.get("identity"))
            if terminal is None or not self.runner.last_report["all_workers_stopped"]:
                raise WorkersNotStopped("FF process closure remains unverified")
            self._persist_closed(uow, terminal)
        except Exception as error:
            # No cleanup guess or unconditional slot release after a failed write.
            logger.error("worker_ff_deferred run_id=%s reason=%s", self.identifier, type(error).__name__)

    def _persist_closed(self, uow, terminal):
        # Retry a transient connection/transaction error with the existing claim only.
        deadline = monotonic() + 3
        while True:
            try:
                with uow() as tx:
                    if terminal.payload.result is None:
                        tx.runs.fail(self.identifier, terminal.payload.diagnostic.termination_reason, claim=self.claim)
                    else:
                        tx.runs.save_result(self.identifier, terminal.payload.result, terminal.residuals, claim=self.claim)
                    # Publish the business result, order state, and closure in a single commit.
                    tx.worker_jobs.finish_if_registered(self.identifier, self.claim)
                return
            except (OperationalError, SnapshotConflict):
                if monotonic() >= deadline:
                    raise
                sleep(.25)

    def start(self, uow):
        self.thread = Thread(target=self.execute, args=(uow,), name=f"standalone-ff-{self.identifier}", daemon=True)
        self.thread.start()
