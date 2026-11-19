"""Finite progress pages. A client waiting between requests holds no server resources."""
from datetime import datetime
from functools import lru_cache
from threading import Lock
from time import monotonic
from typing import Annotated, Literal

from fastapi import Query, Response
from sqlalchemy import text

from app.shared.config import integer_value
from ..application.comparison_v2_contracts import TerminationReason
from ..application.errors import RunError
from .run_schemas import ContractV1

Cursor = Annotated[int | None, Query(ge=0, le=2**63 - 1)]
PageSize = Annotated[int, Query(ge=1, le=32)]
Method = Literal["FF", "BF", "WF", "HF", "M5", "M6-BS"]


class CurrentEngine(ContractV1):
    execution_id: int
    method: Method


class ProgressEvent(ContractV1):
    sequence: int
    kind: Literal["WAITING", "RUNNING", "RECOVERY_BLOCKED", "TERMINAL", "CANCELLATION_REQUESTED",
                  "ENGINE_STARTED", "ENGINE_FINISHED"]
    occurred_at: datetime
    execution_id: int | None
    method: Method | None
    reason: TerminationReason | None


class ProgressPage(ContractV1):
    job_id: int
    profile: Literal["FF", "V2"]
    state: Literal["WAITING", "RUNNING", "RECOVERY_BLOCKED", "TERMINAL"]
    version: int
    cancellation: Literal["NONE", "REQUESTED", "CANCELLED", "COMPLETED_BEFORE_CANCEL"]
    result_state: Literal["EN_EJECUCION", "COMPLETADA", "SIN_SOLUCION", "FALLIDA"]
    result_url: str
    current_engine: CurrentEngine | None
    next_sequence: int
    has_more: bool
    events: list[ProgressEvent]
    poll_after_ms: Literal[1000] = 1000


class ReaderBudget:
    def __init__(self, parallel, rate):
        self.parallel, self.rate = parallel, rate
        self._active, self._tokens, self._refill_at = 0, float(rate), monotonic()
        self._lock = Lock()

    def acquire(self):
        with self._lock:
            now = monotonic()
            self._tokens = min(self.rate, self._tokens + (now - self._refill_at) * self.rate)
            self._refill_at = now
            if self._active == self.parallel or self._tokens < 1:
                error = RunError("PROGRESS_BUSY", "Hay demasiadas consultas de progreso. Reintenta en un segundo.")
                error.retry_after = 1
                raise error
            self._active += 1
            self._tokens -= 1

    def release(self):
        with self._lock:
            self._active -= 1


@lru_cache(maxsize=1)
def reader_budget():
    parallel = integer_value("NEWGLASS_PROGRESS_MAX_READERS", default=4)
    rate = integer_value("NEWGLASS_PROGRESS_READS_PER_SECOND", default=24)
    if parallel > 32 or rate > 200:
        raise RuntimeError("El presupuesto de lectores de progreso supera el máximo permitido.")
    return ReaderBudget(parallel, rate)


async def admit_progress_reader():
    budget = reader_budget()
    budget.acquire()
    try:
        yield
    finally:
        budget.release()


def read_progress(db, identifier, actor_id, *, profile, after_sequence, limit, response: Response):
    from ..infrastructure.progress import ProgressJournal
    from ..infrastructure.worker_jobs import HEARTBEAT_LIMIT

    # Auth and progress share one short-lived session. Pin the page to the observed high-water mark.
    db.execute(text("SET LOCAL statement_timeout = '1000ms'"))
    data = ProgressJournal(db).read(identifier, actor_id, profile=profile,
        after_sequence=after_sequence, limit=limit, heartbeat_limit=HEARTBEAT_LIMIT)
    response.headers["Cache-Control"] = "no-store"
    return ProgressPage(**data)
