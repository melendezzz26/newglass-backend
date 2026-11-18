"""Durable ownership for the single comparison slot in the worker pilot."""
from datetime import timedelta

from sqlalchemy import func, select, update

from app.models import Optimizacion, OptimizationWorkerJob as Job, OptimizationWorkerPolicy as Policy
from ..application.errors import RunError

HEARTBEAT_LIMIT = timedelta(seconds=10)


def pilot_enabled(session, *, protect_admission=False):
    query = select(Policy.pilot).where(Policy.id == 1)
    if protect_admission:
        query = query.with_for_update(read=True)
    return session.scalar(query) is True


def require_legacy_admission(session):
    if pilot_enabled(session, protect_admission=True):
        raise RunError("RESOURCE_UNAVAILABLE", "FF y V1 están deshabilitados en el modo worker V2.",
                       details={"request_accepted": False})
    open_v2 = select(Optimizacion.id_optimizacion).where(Optimizacion.estado == "EN_EJECUCION",
        Optimizacion.criterio_seleccion_version == "CMP-v2").exists()
    if session.scalar(select(open_v2)):
        raise RunError("RECOVERY_BLOCKED", "Hay comparaciones V2 anteriores sin cierre acreditado.")


def require_pilot(session, *, protect_admission=False):
    if not pilot_enabled(session, protect_admission=protect_admission):
        raise RunError("RESOURCE_UNAVAILABLE", "El servicio separado de comparación V2 aún no está habilitado.",
                       details={"request_accepted": False})


class WorkerJobs:
    def __init__(self, session):
        self.session = session

    def require_pilot(self):
        require_pilot(self.session, protect_admission=True)

    def enqueue(self, comparison_id):
        self.session.add(Job(comparison_id=comparison_id, state="WAITING"))

    def _registered(self, identifier, *, lock=False):
        query = select(Job).where(Job.comparison_id == identifier)
        if lock:
            query = query.with_for_update(of=Job).execution_options(populate_existing=True)
            job = self.session.scalar(query)
            return (job, self.session.scalar(select(func.clock_timestamp()))) if job is not None else None
        return self.session.execute(query.add_columns(func.clock_timestamp())).one_or_none()

    @staticmethod
    def _fresh(job, timestamp):
        return job.state == "RUNNING" and job.heartbeat_at > timestamp - HEARTBEAT_LIMIT

    def _assert_owner(self, job, timestamp, token):
        if job.owner_token != token or not self._fresh(job, timestamp):
            raise RunError("RECOVERY_BLOCKED", "La plaza requiere acreditar el cierre del propietario anterior.")

    def require_owner_if_registered(self, identifier, token):
        entry = self._registered(identifier, lock=True)
        if entry is not None:
            self._assert_owner(*entry, token)

    def status(self, identifier):
        entry = self._registered(identifier)
        if entry is None:
            return None
        job, timestamp = entry
        if job.state == "RUNNING" and not self._fresh(job, timestamp):
            return "RECOVERY_BLOCKED"
        return job.state

    def block_stale(self):
        self.session.execute(update(Job).where(Job.state == "RUNNING",
            Job.heartbeat_at <= func.clock_timestamp() - HEARTBEAT_LIMIT).values(state="RECOVERY_BLOCKED"))

    def untracked_open(self):
        registered = select(Job.comparison_id).where(Job.comparison_id == Optimizacion.id_optimizacion).exists()
        return self.session.scalars(select(Optimizacion.id_optimizacion).where(
            Optimizacion.estado == "EN_EJECUCION", ~registered).order_by(Optimizacion.id_optimizacion)).all()

    def next_waiting(self):
        occupied = select(Job.comparison_id).where(Job.slot == 1).exists()
        if self.session.scalar(select(occupied)):
            return None
        waiting = self.session.scalar(select(Job).where(Job.state == "WAITING")
            .order_by(Job.comparison_id).limit(1).with_for_update(skip_locked=True))
        # Historic open work is ambiguous until reviewed. Never replace its owner.
        return waiting if waiting is not None and not self.untracked_open() else None

    def claim(self, job, token):
        if job.state != "WAITING":
            raise RunError("STATE_CONFLICT", "El trabajo ya fue reclamado.")
        timestamp = self.session.scalar(select(func.clock_timestamp()))
        job.state, job.slot, job.owner_token, job.heartbeat_at = "RUNNING", 1, token, timestamp
        self.session.flush()

    def heartbeat(self, identifier, token):
        entry = self._registered(identifier, lock=True)
        if entry is None:
            raise RunError("RECOVERY_BLOCKED", "El trabajo operativo no existe.")
        job, timestamp = entry
        self._assert_owner(job, timestamp, token)
        job.heartbeat_at = timestamp
        return self.session.scalar(select(Optimizacion.comparison_snapshot["cancellation"]["status"].as_string())
            .where(Optimizacion.id_optimizacion == identifier)) == "REQUESTED"

    def finish_if_registered(self, identifier, token):
        entry = self._registered(identifier, lock=True)
        if entry is None:
            return
        job, timestamp = entry
        if not (job.state == "WAITING" and token is None):
            self._assert_owner(job, timestamp, token)
        job.state, job.slot = "TERMINAL", None


def enable_pilot(tx):
    """One-way cutover; run after stopping binaries that predate the policy gate."""
    tx.comparisons_v2.lock_queue()
    policy = tx.session.scalar(select(Policy).where(Policy.id == 1).with_for_update())
    if policy is None:
        raise RunError("RESOURCE_UNAVAILABLE", "La migración de política del worker aún no está aplicada.")
    if policy.pilot:
        return 0
    adopted = 0
    for identifier in tx.worker_jobs.untracked_open():
        row = tx.comparisons_v2._lock(identifier)
        if row.criterio_seleccion_version != "CMP-v2":
            raise RunError("RECOVERY_BLOCKED", "Existe un cálculo legacy abierto; acredita su cierre antes de activar el piloto.")
        try:
            current = tx.comparisons_v2.get(identifier)
        except RunError as error:
            if error.code != "INTERNAL_ERROR":
                raise
            raise RunError("RECOVERY_BLOCKED", "Una comparación anterior tiene trazabilidad inválida.") from error
        if not current.waiting:
            raise RunError("RECOVERY_BLOCKED", "Existe una comparación anterior con cierre sin acreditar.")
        tx.worker_jobs.enqueue(identifier)
        adopted += 1
    policy.pilot = True
    return adopted
