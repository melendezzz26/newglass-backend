"""Small, transactional progress journal for the separated worker."""
from datetime import timedelta

from sqlalchemy import case, delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from app.models import (EjecucionOptimizacion as Execution, Optimizacion as Run,
    OptimizationProgressEvent as ProgressEvent, OptimizationProgressStream as Stream, OptimizationWorkerJob as Job)
from app.shared.config import integer_value
from ..application.errors import RunError


def progress_retention():
    count = integer_value("NEWGLASS_PROGRESS_RETAINED_EVENTS", default=64)
    seconds = integer_value("NEWGLASS_PROGRESS_TTL_SECONDS", default=3600)
    if count > 256 or seconds > 86400:
        raise RuntimeError("El progreso admite hasta 256 eventos por trabajo y una retención de 24 horas.")
    return count, timedelta(seconds=seconds)


class ProgressJournal:
    def __init__(self, session):
        self.session = session

    def ensure_engine_started(self, job_id, execution_id):
        # Called under the job lock. An uncertain commit can be retried without a second milestone.
        active_execution = self.session.scalar(select(Stream.active_execution_id).where(Stream.job_id == job_id))
        if active_execution != execution_id:
            self.append(job_id, "ENGINE_STARTED", execution_id=execution_id)

    def append(self, job_id, kind, *, execution_id=None, reason=None):
        # The calling transition has already locked or created the job and validated any worker claim.
        keep, _ = progress_retention()
        opened = execution_id if kind == "ENGINE_STARTED" else None
        active = Stream.active_execution_id
        if kind in ("WAITING", "TERMINAL", "ENGINE_STARTED"):
            active = opened
        elif kind == "ENGINE_FINISHED":
            active = case((Stream.active_execution_id == execution_id, None), else_=active)
        statement = insert(Stream).values(job_id=job_id, last_sequence=1, active_execution_id=opened)
        sequence = self.session.scalar(statement.on_conflict_do_update(index_elements=[Stream.job_id],
            set_={"last_sequence": Stream.last_sequence + 1, "active_execution_id": active})
            .returning(Stream.last_sequence))
        self.session.execute(insert(ProgressEvent).values(job_id=job_id, sequence=sequence, kind=kind,
            execution_id=execution_id, reason=reason))
        if sequence > keep:
            self.session.execute(delete(ProgressEvent).where(
                ProgressEvent.job_id == job_id, ProgressEvent.sequence <= sequence - keep))

    def purge_expired(self):
        _, ttl = progress_retention()
        expired = select(ProgressEvent.job_id, ProgressEvent.sequence).where(
            ProgressEvent.occurred_at < func.statement_timestamp() - ttl
        ).order_by(ProgressEvent.occurred_at).limit(256).with_for_update(skip_locked=True)
        self.session.execute(delete(ProgressEvent).where(tuple_(ProgressEvent.job_id, ProgressEvent.sequence).in_(expired)))

    def read(self, job_id, actor_id, *, profile, after_sequence, limit, heartbeat_limit):
        snapshot = Run.comparison_snapshot
        runtime = snapshot["runtime"]
        current_execution = aliased(Execution)
        query = select(Run.estado.label("result_state"), Run.comparison_version.label("version"),
            snapshot["cancellation"]["status"].as_string().label("cancel_state"),
            snapshot["queue_version"].as_string().label("queue_version"),
            runtime["supervisor"].as_string().label("supervisor"),
            runtime["supervisor_stopped_at"].as_string().label("stopped"),
            Job.state, (Job.heartbeat_at <= func.statement_timestamp() - heartbeat_limit).label("expired"),
            Stream.last_sequence, current_execution.id_ejecucion.label("execution_id"), current_execution.metodo
        ).outerjoin(Job, (Job.comparison_id == Run.id_optimizacion) & (Job.kind == profile)
        ).outerjoin(Stream, Stream.job_id == Job.comparison_id
        ).outerjoin(current_execution, (current_execution.id_ejecucion == Stream.active_execution_id)
                    & (current_execution.id_optimizacion == Run.id_optimizacion)
        ).where(Run.id_optimizacion == job_id, Run.id_usuario_ejecutor == actor_id)
        if profile == "V2":
            query = query.where(Run.criterio_seleccion_version == "CMP-v2", snapshot["profile"].as_string() == "comparison-v2")
        else:
            query = query.where(Run.criterio_seleccion_version == "S1-FF-v1", Run.ff_request_key.is_not(None))
        row = self.session.execute(query).one_or_none()
        if row is None:
            raise RunError("NOT_FOUND", "No se encontró el trabajo de optimización.")
        state = row.state
        if state == "RUNNING" and row.expired:
            state = "RECOVERY_BLOCKED"
        if state is None:
            if profile == "V2" and row.stopped and row.result_state != "EN_EJECUCION":
                state = "TERMINAL"
            elif profile == "V2" and row.queue_version == "1" and row.supervisor is None:
                state = "WAITING"
            else:
                state = "RECOVERY_BLOCKED"
        result_url = (f"/api/optimization/comparisons-v2/{job_id}" if profile == "V2"
                      else f"/api/optimization/runs/{job_id}")
        state_payload = dict(job_id=job_id, profile=profile, state=state, version=row.version,
            cancellation=row.cancel_state or "NONE", result_state=row.result_state, result_url=result_url,
            current_engine=(dict(execution_id=row.execution_id, method=row.metodo) if row.execution_id else None))
        last = row.last_sequence or 0
        if after_sequence is not None and after_sequence > last:
            raise RunError("STATE_CONFLICT", "El cursor de progreso está por delante del trabajo.")
        if last == 0 or after_sequence == last:
            return dict(**state_payload, next_sequence=last, has_more=False, events=[])
        _, ttl = progress_retention()
        entries = self.session.execute(select(ProgressEvent, Execution.metodo).outerjoin(Execution,
            (Execution.id_ejecucion == ProgressEvent.execution_id) & (Execution.id_optimizacion == ProgressEvent.job_id)
        ).where(ProgressEvent.job_id == job_id, ProgressEvent.sequence > (after_sequence or 0), ProgressEvent.sequence <= last,
                ProgressEvent.occurred_at >= func.transaction_timestamp() - ttl
        ).order_by(ProgressEvent.sequence).limit(limit + 1)).all()
        if after_sequence is not None and (not entries or entries[0][0].sequence != after_sequence + 1):
            raise RunError("EVENT_CURSOR_EXPIRED", "El cursor caducó. Recupera el estado persistido del trabajo.",
                           details={"resume_after_sequence": last, "result_url": result_url})
        events = [dict(sequence=event.sequence, kind=event.kind, occurred_at=event.occurred_at,
            execution_id=event.execution_id, method=method, reason=event.reason) for event, method in entries[:limit]]
        return dict(**state_payload, next_sequence=events[-1]["sequence"] if events else last,
                    has_more=len(entries) > limit, events=events)
