"""Durable ownership for the single comparison slot in the worker pilot."""
from dataclasses import dataclass
from datetime import timedelta
import os

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models import Optimizacion, OptimizationWorkerAttempt as Attempt
from app.models import OptimizationWorkerJob as Job, OptimizationWorkerPolicy as Policy
from ..application.admission import OptimizerBusy
from ..application.errors import RunError
from ..application.supervision_contracts import identity
from .process_lifecycle import process_identity, process_scope
from .progress import ProgressJournal
from .stock_protection import protect_stock, release_stock

HEARTBEAT_LIMIT = timedelta(seconds=10)


@dataclass(frozen=True)
class WorkerClaim:
    token: str
    generation: int


@dataclass(frozen=True)
class RecoveredClaim(WorkerClaim):
    """Authorized only by the process evidence check inside the recovery transaction."""


def owner_token(owner):
    return owner.token if isinstance(owner, WorkerClaim) else owner


def execution_policy(session, *, protect_admission=False):
    query = select(Policy).where(Policy.id == 1)
    if protect_admission:
        query = query.with_for_update(read=True)
    mode = session.scalar(query)
    if mode is None:
        raise RunError("RESOURCE_UNAVAILABLE", "La política de ejecución no está disponible.",
                       details={"request_accepted": False})
    return mode


def pilot_enabled(session, *, protect_admission=False):
    return execution_policy(session, protect_admission=protect_admission).pilot


def require_legacy_admission(session, *, v1=False):
    policy = execution_policy(session, protect_admission=True)
    if policy.pilot:
        if v1 and policy.mixed:
            return True
        raise RunError("RESOURCE_UNAVAILABLE", "Esta ruta legacy está deshabilitada en el modo worker.",
                       details={"request_accepted": False})
    open_v2 = select(Optimizacion.id_optimizacion).where(Optimizacion.estado == "EN_EJECUCION",
        Optimizacion.criterio_seleccion_version == "CMP-v2").exists()
    if session.scalar(select(open_v2)):
        raise RunError("RECOVERY_BLOCKED", "Hay comparaciones V2 anteriores sin cierre acreditado.")
    return False


def require_pilot(session, *, protect_admission=False):
    if not pilot_enabled(session, protect_admission=protect_admission):
        raise RunError("RESOURCE_UNAVAILABLE", "El servicio separado de comparación V2 aún no está habilitado.",
                       details={"request_accepted": False})


def require_durable_ff(session):
    if not execution_policy(session, protect_admission=True).mixed:
        raise RunError("RESOURCE_UNAVAILABLE", "FF durable requiere el piloto mixto.",
                       details={"request_accepted": False})


class WorkerJobs:
    def __init__(self, session):
        self.session = session
        self.progress = ProgressJournal(session)

    def require_pilot(self):
        require_pilot(self.session, protect_admission=True)

    def require_durable_ff(self):
        require_durable_ff(self.session)

    def enqueue(self, comparison_id, problem, *, kind="V2"):
        self.session.add(Job(comparison_id=comparison_id, state="WAITING", kind=kind))
        self.session.flush()
        protect_stock(self.session, comparison_id, problem)
        self.progress.append(comparison_id, "WAITING")

    def waiting_count(self):
        return self.session.scalar(select(func.count()).select_from(Job).where(Job.state == "WAITING"))

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

    def _assert_owner(self, job, timestamp, claim, *, recovery=False):
        permitted = job.state == "RECOVERY_BLOCKED" if recovery else self._fresh(job, timestamp)
        if (not isinstance(claim, WorkerClaim) or isinstance(claim, RecoveredClaim) != recovery
                or (job.owner_token, job.generation) != (claim.token, claim.generation) or not permitted):
            raise RunError("RECOVERY_BLOCKED", "La plaza requiere acreditar el cierre del propietario anterior.")
        attempt = self.session.get(Attempt, (job.comparison_id, job.generation))
        if attempt is None or attempt.closed_at is not None or attempt.owner_token != claim.token:
            raise RunError("RECOVERY_BLOCKED", "El intento activo no tiene una trazabilidad válida.")
        return attempt

    def require_owner_if_registered(self, identifier, claim, *, required=False, without_dispatch=False, kind=None):
        entry = self._registered(identifier, lock=True)
        if entry is not None:
            if kind is not None and entry[0].kind != kind:
                raise RunError("RECOVERY_BLOCKED", "El tipo de trabajo operativo no coincide.")
            if without_dispatch and claim is None and entry[0].state == "WAITING":
                return True
            self._assert_owner(*entry, claim, recovery=isinstance(claim, RecoveredClaim))
            return True
        elif required or isinstance(claim, WorkerClaim):
            raise RunError("RECOVERY_BLOCKED", "El trabajo operativo no está disponible.")
        return False

    def status(self, identifier):
        entry = self._registered(identifier)
        if entry is None:
            return None
        job, timestamp = entry
        if job.state == "RUNNING" and not self._fresh(job, timestamp):
            return "RECOVERY_BLOCKED"
        return job.state

    def block_stale(self):
        blocked = self.session.execute(update(Job).where(Job.state == "RUNNING",
            Job.heartbeat_at <= func.clock_timestamp() - HEARTBEAT_LIMIT).values(state="RECOVERY_BLOCKED")
            .returning(Job.comparison_id, Job.kind))
        for identifier, kind in blocked:
            if kind in ("FF", "V2"):
                self.progress.append(identifier, "RECOVERY_BLOCKED")

    def untracked_open(self):
        registered = select(Job.comparison_id).where(Job.comparison_id == Optimizacion.id_optimizacion).exists()
        return self.session.scalars(select(Optimizacion.id_optimizacion).where(
            Optimizacion.estado == "EN_EJECUCION", ~registered).order_by(Optimizacion.id_optimizacion)).all()

    def occupied(self):
        return self.session.scalar(select(select(Job.comparison_id).where(Job.slot == 1).exists()))

    def next_waiting(self):
        if self.occupied():
            return None
        waiting = self.session.scalar(select(Job).where(Job.state == "WAITING")
            .order_by(Job.comparison_id).limit(1).with_for_update(skip_locked=True))
        # Historic open work is ambiguous until reviewed. Never replace its owner.
        return waiting if waiting is not None and not self.untracked_open() else None

    def _record_attempt(self, identifier, claim, timestamp):
        identity = process_identity(os.getpid())
        if identity is None:
            raise RunError("RECOVERY_BLOCKED", "No se pudo acreditar la identidad Linux del propietario.")
        self.session.add(Attempt(comparison_id=identifier, generation=claim.generation, owner_token=claim.token,
                                 owner_identity=identity, owner_scope=process_scope(), started_at=timestamp))

    def claim(self, job, token):
        if job.state != "WAITING":
            raise RunError("STATE_CONFLICT", "El trabajo ya fue reclamado.")
        timestamp = self.session.scalar(select(func.clock_timestamp()))
        job.state, job.slot, job.owner_token, job.heartbeat_at = "RUNNING", 1, token, timestamp
        job.generation += 1
        claim = WorkerClaim(token, job.generation)
        self._record_attempt(job.comparison_id, claim, timestamp)
        self.session.flush()
        self.progress.append(job.comparison_id, "RUNNING")
        return claim

    def reserve_v1(self, identifier, token, problem):
        if self.untracked_open() != [identifier]:
            raise RunError("RECOVERY_BLOCKED", "Existe otro cálculo sin dueño operativo acreditado.",
                           details={"request_accepted": False})
        timestamp = self.session.scalar(select(func.clock_timestamp()))
        inserted = self.session.scalar(insert(Job).values(comparison_id=identifier, kind="V1", state="RUNNING",
            generation=1, slot=1, owner_token=token, heartbeat_at=timestamp)
            .on_conflict_do_nothing(constraint="uq_optimization_worker_global_slot").returning(Job.comparison_id))
        if inserted is None:
            error = OptimizerBusy("active_runs", 1, 1)
            error.details = {**error.details, "request_accepted": False}
            raise error
        self._record_attempt(identifier, WorkerClaim(token, 1), timestamp)
        protect_stock(self.session, identifier, problem)

    def lookup_v1_claim(self, identifier, token):
        job = self.session.get(Job, identifier)
        if job is None:
            return False, None
        return True, WorkerClaim(token, job.generation) if job.kind == "V1" and job.owner_token == token else None

    def heartbeat(self, identifier, claim):
        entry = self._registered(identifier, lock=True)
        if entry is None:
            raise RunError("RECOVERY_BLOCKED", "El trabajo operativo no existe.")
        job, timestamp = entry
        self._assert_owner(job, timestamp, claim)
        job.heartbeat_at = timestamp
        if job.kind == "FF":
            return False  # Standalone FF has no public cancellation command.
        return self.session.scalar(select(Optimizacion.comparison_snapshot["cancellation"]["status"].as_string())
            .where(Optimizacion.id_optimizacion == identifier)) == "REQUESTED"

    def record_ff_dispatch(self, identifier, claim, *, process=None):
        entry = self._registered(identifier, lock=True)
        if entry is None or entry[0].kind != "FF":
            raise RunError("RECOVERY_BLOCKED", "No se encontró el intento operativo de FF.")
        job, timestamp = entry
        attempt = self._assert_owner(job, timestamp, claim)
        if process is None and attempt.dispatch_at is None:
            attempt.dispatch_at = timestamp
        elif process is not None and attempt.dispatch_at is not None and attempt.worker_identity is None:
            attempt.worker_identity = identity(process)
        else:
            raise RunError("RECOVERY_BLOCKED", "La traza de despacho de FF no coincide.")

    def require_ff_join(self, identifier, claim):
        entry = self._registered(identifier, lock=True)
        if entry is None or entry[0].kind != "FF":
            raise RunError("RECOVERY_BLOCKED", "No se encontró el intento operativo de FF.")
        attempt = self._assert_owner(*entry, claim)
        if attempt.dispatch_at is None or attempt.worker_identity is None:
            raise RunError("RECOVERY_BLOCKED", "FF carece de identidad de proceso acreditada.")

    def ff_closed_for_confirmation(self, identifier):
        return self.session.scalar(select(select(Job.comparison_id).join(Attempt,
            (Attempt.comparison_id == Job.comparison_id) & (Attempt.generation == Job.generation)
            & (Attempt.owner_token == Job.owner_token)).where(Job.comparison_id == identifier,
            Job.kind == "FF", Job.state == "TERMINAL", Attempt.closed_at.is_not(None),
            Attempt.closure == "JOINED", Attempt.dispatch_at.is_not(None),
            Attempt.worker_identity.is_not(None)).exists()))

    def finish_if_registered(self, identifier, claim, *, closure="JOINED"):
        entry = self._registered(identifier, lock=True)
        if entry is None:
            return
        job, timestamp = entry
        if not (job.state == "WAITING" and claim is None):
            attempt = self._assert_owner(job, timestamp, claim, recovery=isinstance(claim, RecoveredClaim))
            attempt.closed_at, attempt.closure = timestamp, closure
        job.state, job.slot = "TERMINAL", None
        release_stock(self.session, identifier)
        if job.kind in ("FF", "V2"):
            self.progress.append(identifier, "TERMINAL")

    def blocked(self, kind):
        return self.session.scalar(select(Job).where(Job.slot == 1, Job.kind == kind,
            Job.state == "RECOVERY_BLOCKED").with_for_update(skip_locked=True))

    def requeue_proven(self, identifier, claim):
        job, timestamp = self._registered(identifier, lock=True)
        attempt = self._assert_owner(job, timestamp, claim, recovery=True)
        attempt.closed_at, attempt.closure = timestamp, "NO_DISPATCH"
        job.state, job.slot, job.owner_token, job.heartbeat_at = "WAITING", None, None, None
        self.progress.append(identifier, "WAITING")


def enable_pilot(tx, *, mixed=False):
    """One-way cutover; run after stopping binaries that predate the policy gate."""
    tx.comparisons_v2.lock_queue()
    policy = tx.session.scalar(select(Policy).where(Policy.id == 1).with_for_update())
    if policy is None:
        raise RunError("RESOURCE_UNAVAILABLE", "La migración de política del worker aún no está aplicada.")
    if policy.mixed or (policy.pilot and not mixed):
        return 0
    if mixed and tx.worker_jobs.occupied():
        raise RunError("RECOVERY_BLOCKED", "Acredita el cierre de la plaza activa antes de cambiar el piloto.")
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
        tx.worker_jobs.enqueue(identifier, current.problem)
        adopted += 1
    policy.pilot = True
    policy.mixed = mixed
    return adopted
