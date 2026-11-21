"""Two independent Uvicorn processes and competing real Linux workers on temporary PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from functools import partial
import os
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import EjecucionOptimizacion, Optimizacion, OptimizationWorkerAttempt, OptimizationWorkerJob, Pedido, RolPermiso
from app.modules.optimization.application.comparison_acceptance_v2 import AcceptComparisonV2
from app.modules.optimization.application.comparison_v2_contracts import ExecutionDiagnostic, ExecutionPayloadV2
from app.modules.optimization.application.errors import RunError
from app.modules.optimization.infrastructure.comparison_v2_uow import SqlComparisonPersistenceUnitOfWork
from app.modules.optimization.infrastructure.process_lifecycle import process_identity, process_scope
from app.modules.optimization.infrastructure.worker_jobs import WorkerClaim, enable_pilot, pilot_enabled, require_legacy_admission
from app.modules.optimization.presentation.comparison_supervision_v2 import authorize_actor
from tests.integration.postgres_support import BACKEND, downgrade, new_database, upgrade
from .test_comparison_queue_v2 import BASE, inventory, prepare_queue_data


def wait_for(observe, predicate, *, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = observe()
        if predicate(value):
            return value
        time.sleep(.05)
    pytest.fail(f"Condition did not become true in {seconds}s. Last value: {value}")


class Services:
    def __init__(self, engine, path, stack):
        assert engine.url.host == "127.0.0.1" and engine.url.username == "r1_test"
        assert engine.url.database.startswith("r1_")
        self.path, self.stack = path, stack
        self.environment = dict(os.environ, PYTHON_DOTENV_DISABLED="1",
                                DATABASE_URL=engine.url.render_as_string(hide_password=False))
        self.workers = {}
        self.resume_file = path / "resume-ff"
        self.v1_resume = path / "resume-v1"
        self.v1_ready = path / "v1-ready"

    def start(self, name, args, **environment):
        log = self.stack.enter_context((self.path / f"{name}.log").open("w", encoding="utf8"))
        process = subprocess.Popen([sys.executable, *args], cwd=BACKEND, stdout=log, stderr=subprocess.STDOUT,
                                   env={**self.environment, **environment, "PGAPPNAME": name})
        self.stack.callback(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                pytest.fail("A test-owned service required a forced shutdown")

    def api(self, name, *, hold_v1=False):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        if hold_v1:
            command = f"from tests.e2e_cancellation_worker import run_gated_v1_api; run_gated_v1_api({port})"
            process = self.start(name, ["-c", command], QA_RECOVERY_READY=str(self.v1_ready),
                                 QA_RESUME_ENGINE=str(self.v1_resume))
        else:
            process = self.start(name, ["-m", "uvicorn", "main:app", "--host", "127.0.0.1",
                                       "--port", str(port), "--workers", "1", "--no-access-log"])
        client = self.stack.enter_context(httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=8))

        def ready():
            assert process.poll() is None, self.read_log(name)
            try:
                return client.get("/health/ready").status_code == 200
            except httpx.TransportError:
                return False

        wait_for(ready, bool, seconds=25)
        return process, client

    def worker(self, name, *, hold=None, method="FF", pause_ff_started_event=False):
        if hold is None:
            args, environment = ["-m", "app.modules.optimization.worker_v2"], {}
        else:
            args = ["-c", "from tests.e2e_cancellation_worker import run_gated_worker; run_gated_worker()"]
            environment = {"QA_HOLD_ORDER": str(hold), "QA_RESUME_ENGINE": str(self.resume_file), "QA_HOLD_METHOD": method}
            if pause_ff_started_event:
                environment.update(QA_PAUSE_FF_STARTED_EVENT="1", QA_RECOVERY_READY=str(self.path / f"{name}-ready"))
        process = self.start(name, args, **environment)
        if pause_ff_started_event:
            self.stack.callback(self.resume_file.touch)
        self.workers[process.pid] = process
        return process

    def recovery_worker(self, name, phase):
        ready = self.path / f"{name}-ready"
        args = ["-c", "from tests.e2e_cancellation_worker import run_recovery_worker; run_recovery_worker()"]
        process = self.start(name, args, QA_RECOVERY_READY=str(ready), QA_RESUME_ENGINE=str(self.path / f"{name}-resume"),
                             QA_RECOVERY_PHASE=phase)
        wait_for(ready.exists, bool)
        return process

    def read_log(self, name):
        return (self.path / f"{name}.log").read_text(encoding="utf8", errors="replace")


@pytest.fixture
def worker_db(isolated_postgres, tmp_path):
    with new_database(isolated_postgres) as engine, ExitStack() as stack:
        result = upgrade(engine, "head")
        assert result.returncode == 0, result.stderr
        data = prepare_queue_data(engine)
        data.engine = engine
        data.uow = partial(SqlComparisonPersistenceUnitOfWork, data.sessions)
        data.services = Services(engine, tmp_path, stack)
        data.cluster = isolated_postgres
        yield data


def accept(client, data, actor, *, order=None, key="qa-separated-01"):
    return client.post(BASE, headers={**data.headers[actor], "Idempotency-Key": key},
                       json={"order_ids": [order or data.orders[actor]]})


def owned_get(client, data, actor, identifier, suffix=""):
    return client.get(f"{BASE}/{identifier}{suffix}", headers=data.headers[actor])


def summary(client, data, actor, identifier):
    response = owned_get(client, data, actor, identifier)
    assert response.status_code == 200, response.text
    return response.json()


def queue_state(client, data, actor, identifier):
    response = owned_get(client, data, actor, identifier, "/queue")
    assert response.status_code == 200, response.text
    return response.json()["state"]


def wait_running(client, data, actor, identifier):
    return wait_for(lambda: summary(client, data, actor, identifier),
                    lambda value: value["executions"][0]["dispatch_state"] == "RUNNING")


def cancellation(client, data, actor, current):
    return client.post(f"{BASE}/{current['comparison_id']}/cancel",
                       headers={**data.headers[actor], "Idempotency-Key": "qa-cancel-separated"},
                       json={"expected_version": current["version"]})


def activate(data, *, mixed=False):
    with data.uow() as tx:
        return enable_pilot(tx, mixed=mixed)


def test_two_apis_two_reclaimers_and_api_restart_do_not_interrupt_calculation(worker_db):
    data = worker_db
    assert activate(data) == 0
    a_server, api_a = data.services.api("api-a")
    b_server, api_b = data.services.api("api-b")
    assert api_b.get("/api/inventory/planchas", headers=data.headers["L"]).status_code == 200
    assert accept(api_b, data, "L").status_code == 403
    assert api_a.post("/api/optimization/runs", headers=data.headers["A"],
                      json={"order_ids": [data.orders["A"]]}).status_code == 503
    assert api_b.post("/api/optimization/comparisons",
                      headers={**data.headers["O"], "Idempotency-Key": "qa-legacy-off"},
                      json={"order_ids": [data.orders["O"]]}).status_code == 503
    with ThreadPoolExecutor(2) as requests:
        a, b = [f.result() for f in (requests.submit(accept, api_a, data, "A"),
                                     requests.submit(accept, api_b, data, "O"))]
    assert a.status_code == b.status_code == 201
    ids = {"A": a.json()["comparison_id"], "O": b.json()["comparison_id"]}
    first, second = sorted(ids, key=ids.get)
    assert accept(api_b, data, first).json()["comparison_id"] == ids[first]
    assert accept(api_b, data, second, order=data.orders[first], key="qa-order-conflict").status_code == 409
    assert owned_get(api_a, data, "A", ids["O"], "/queue").status_code == 404
    assert owned_get(api_b, data, "O", ids["A"]).status_code == 404
    assert api_b.post(f"{BASE}/{ids['A']}/confirm", headers={**data.headers["L"], "Idempotency-Key": "qa-denied-confirm"},
        json={"execution_id": a.json()["executions"][0]["execution_id"], "expected_version": a.json()["version"]}).status_code == 403
    data.services.worker("worker-one", hold=data.orders[first])
    data.services.worker("worker-two", hold=data.orders[first])
    active = wait_running(api_b, data, first, ids[first])
    for name in ("worker-one", "worker-two"):
        wait_for(lambda: data.services.read_log(name), lambda log: "worker_v2_ready" in log)
    assert queue_state(api_a, data, second, ids[second]) == "WAITING"
    assert owned_get(api_a, data, first, ids[first], "/events").json()["detail"]["code"] == "VISUAL_DATA_UNAVAILABLE"
    with data.sessions() as db:
        slot = db.scalar(select(OptimizationWorkerJob).where(OptimizationWorkerJob.slot == 1))
        assert slot.comparison_id == ids[first] and slot.state == "RUNNING"
        owner = db.get(Optimizacion, ids[first]).comparison_snapshot["runtime"]["supervisor"]
        assert owner["token"] == slot.owner_token and owner["identity"]["pid"] in data.services.workers
        assert owner["identity"]["pid"] != a_server.pid
        engine_identity = db.get(Optimizacion, ids[first]).comparison_snapshot["runtime"]["workers"][
            str(active["executions"][0]["execution_id"])]
        connections = db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                                  "AND application_name IN ('worker-one', 'worker-two')"))
        assert 1 <= connections <= 4
        open_during_calculation = db.scalar(text("SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND application_name IN ('worker-one', 'worker-two') "
            "AND state = 'idle in transaction' AND clock_timestamp() - xact_start > interval '1 second'"))
        assert open_during_calculation == 0
    with pytest.raises(IntegrityError) as duplicated:
        with data.sessions.begin() as db:
            contender = db.get(OptimizationWorkerJob, ids[second])
            contender.state, contender.slot, contender.owner_token = "RUNNING", 1, uuid4().hex
            contender.heartbeat_at = datetime.now(timezone.utc)
            contender.generation = 1
    assert duplicated.value.orig.diag.constraint_name in (
        "uq_optimization_worker_global_slot", "ex_optimization_worker_exclusive_legacy")

    data.services.stop(a_server)
    data.services.stop(b_server)
    _, restarted = data.services.api("api-a-restarted")
    _, api_b = data.services.api("api-b-restarted")
    assert queue_state(restarted, data, first, ids[first]) == "RUNNING"
    assert process_identity(engine_identity["pid"]) == engine_identity
    data.services.resume_file.touch()
    done = {}
    for actor in (first, second):
        identifier = ids[actor]
        wait_for(lambda: queue_state(restarted, data, actor, identifier), lambda state: state == "TERMINAL")
        done[actor] = summary(api_b, data, actor, identifier)
        assert summary(restarted, data, actor, identifier) == done[actor]
        assert all(job["complete"] for job in done[actor]["executions"])
        assert all(owned_get(restarted, data, actor, identifier,
                   f"/executions/{job['execution_id']}").json()["result"] is not None for job in done[actor]["executions"])
    with data.sessions() as db:
        assert db.scalar(select(func.count()).select_from(EjecucionOptimizacion)) == 12
        assert db.scalar(select(func.count()).select_from(OptimizationWorkerJob).where(OptimizationWorkerJob.slot == 1)) == 0
        assert db.get(OptimizationWorkerJob, ids[first]).owner_token == owner["token"]
        assert db.get(OptimizationWorkerJob, ids[second]).owner_token != owner["token"]
        assert {row.patron_resultado["snapshot"]["snapshot_version"]
                for row in db.scalars(select(EjecucionOptimizacion))} == {4}
    assert inventory(data) == (1, 0, 0)
    print(f"worker_connections_during_first_run={connections} comparisons=2 finished_engines=12 global_slots=1")
    measurements = wait_for(lambda: [line for name in ("worker-one", "worker-two")
        for line in data.services.read_log(name).splitlines() if "worker_v2_closed" in line], lambda rows: len(rows) == 2)
    print("\n".join(measurements))

    def select_ff(actor, client):
        current = done[actor]
        response = client.put(f"{BASE}/{ids[actor]}/selection", headers=data.headers[actor],
            json={"execution_id": current["executions"][0]["execution_id"], "expected_version": current["version"]})
        assert response.status_code == 200, response.text
        return response.json()

    a, b = select_ff("A", restarted), select_ff("O", api_b)

    def confirm(actor, client, current):
        return client.post(f"{BASE}/{ids[actor]}/confirm",
            headers={**data.headers[actor], "Idempotency-Key": "qa-separated-confirm"},
            json={"execution_id": current["selected_execution_id"], "expected_version": current["version"]})

    with ThreadPoolExecutor(2) as requests:
        confirmed = [f.result() for f in (requests.submit(confirm, "A", restarted, a),
                                          requests.submit(confirm, "O", api_b, b))]
    assert sorted(response.status_code for response in confirmed) == [200, 409]
    winner, state = ("A", a) if confirmed[0].status_code == 200 else ("O", b)
    repeated = confirm(winner, api_b, state)
    receipt = next(response.json() for response in confirmed if response.status_code == 200)
    assert repeated.status_code == 200 and repeated.json() == receipt
    assert inventory(data) == (0, 1, 1)


def test_dead_worker_keeps_slot_and_fences_stale_token(worker_db):
    data = worker_db
    activate(data)
    _, client = data.services.api("api-crash")
    first = accept(client, data, "A").json()["comparison_id"]
    second = accept(client, data, "O").json()["comparison_id"]
    a = data.services.worker("worker-crash-a", hold=data.orders["A"])
    b = data.services.worker("worker-crash-b", hold=data.orders["A"])
    running = wait_running(client, data, "A", first)
    with data.sessions() as db:
        owner = db.get(Optimizacion, first).comparison_snapshot["runtime"]["supervisor"]
    worker = {a.pid: a, b.pid: b}[owner["identity"]["pid"]]
    worker.send_signal(signal.SIGKILL)
    worker.wait(timeout=5)
    wait_for(lambda: queue_state(client, data, "A", first), lambda status: status == "RECOVERY_BLOCKED")
    def persisted_state():
        with data.sessions() as db:
            return db.get(OptimizationWorkerJob, first).state

    wait_for(persisted_state, lambda state: state == "RECOVERY_BLOCKED")
    progress = owned_get(client, data, "A", first, "/progress")
    assert progress.status_code == 200 and progress.json()["state"] == "RECOVERY_BLOCKED"
    assert progress.json()["events"][-1]["kind"] == "RECOVERY_BLOCKED"
    assert queue_state(client, data, "O", second) == "WAITING"
    assert summary(client, data, "A", first)["terminal"] is False
    cancel_response = cancellation(client, data, "A", summary(client, data, "A", first))
    assert cancel_response.status_code == 202 and cancel_response.json()["cancel_state"] == "REQUESTED"
    for stale_token in (owner["token"], uuid4().hex):
        with pytest.raises(RunError) as refused:
            with data.uow() as tx:
                tx.comparisons_v2.record_worker(first, running["executions"][0]["execution_id"], stale_token, None)
        assert refused.value.code in ("RECOVERY_BLOCKED", "STATE_CONFLICT")
    with data.sessions() as db:
        held = db.get(OptimizationWorkerJob, first)
        assert held.owner_token == owner["token"] and held.slot == 1
        assert db.get(Pedido, data.orders["A"]).estado == "EN_OPTIMIZACION"
    assert inventory(data) == (1, 0, 0)


def test_durable_active_and_waiting_cancellation_permission_revocation(worker_db):
    data = worker_db
    activate(data)
    _, client = data.services.api("api-cancel")
    first = accept(client, data, "A").json()["comparison_id"]
    waiting = accept(client, data, "O").json()
    revoked = accept(client, data, "O", order=data.orders["L"], key="qa-revocation").json()["comparison_id"]
    assert cancellation(client, data, "O", waiting).json()["cancel_state"] == "CANCELLED"
    with data.sessions.begin() as db:
        db.execute(delete(RolPermiso).where(RolPermiso.id_rol == data.role_ids["O"],
                                           RolPermiso.id_permiso == data.codes["optimization.execute"]))
    data.services.worker("worker-cancel", hold=data.orders["A"])
    current = wait_running(client, data, "A", first)
    response = cancellation(client, data, "A", current)
    assert response.status_code == 202 and response.json()["cancel_state"] == "REQUESTED"
    wait_for(lambda: queue_state(client, data, "A", first), lambda status: status == "TERMINAL")
    assert summary(client, data, "A", first)["cancellation_status"] == "CANCELLED"
    wait_for(lambda: queue_state(client, data, "O", revoked), lambda status: status == "TERMINAL")
    denial = summary(client, data, "O", revoked)
    assert all(job["diagnostic"]["statistics"] == {"queue_permission_revoked": 1} for job in denial["executions"])
    assert inventory(data) == (1, 0, 0)


def test_temporary_database_outage_never_reassigns_a_live_job(worker_db):
    data = worker_db
    activate(data)
    _, client = data.services.api("api-outage")
    first = accept(client, data, "A").json()["comparison_id"]
    second = accept(client, data, "O").json()["comparison_id"]
    worker = data.services.worker("worker-outage", hold=data.orders["A"])
    running = wait_running(client, data, "A", first)
    database = data.engine.url.database
    admin = create_engine(data.cluster, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as control:
            assert database.startswith("r1_") and database[3:].isalnum()
            control.exec_driver_sql(f'ALTER DATABASE "{database}" WITH ALLOW_CONNECTIONS false')
            try:
                control.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                     "WHERE datname = :database AND application_name = 'worker-outage'"),
                                {"database": database})
                wait_for(lambda: data.services.read_log("worker-outage"),
                         lambda log: "worker_v2_deferred reason=OperationalError" in log, seconds=6)
                assert worker.poll() is None
            finally:
                control.exec_driver_sql(f'ALTER DATABASE "{database}" WITH ALLOW_CONNECTIONS true')
    finally:
        admin.dispose()
    assert running["terminal"] is False
    wait_for(lambda: queue_state(client, data, "A", first), lambda state: state == "TERMINAL")
    wait_for(lambda: queue_state(client, data, "O", second), lambda state: state == "TERMINAL")
    assert all(e["complete"] for e in summary(client, data, "O", second)["executions"])
    assert inventory(data) == (1, 0, 0)


def accept_2a(data, actor, key):
    return AcceptComparisonV2(data.uow, authorize_actor).execute([data.orders[actor]], data.actors[actor],
                                                               key, queue_capacity=8)


def test_cutover_adopts_only_waiting_2a_without_changing_history(worker_db):
    data = worker_db
    earlier = accept_2a(data, "A", "historical-wait").comparison_id
    _, client = data.services.api("api-cutover")
    assert queue_state(client, data, "A", earlier) == "WAITING"
    with data.sessions() as db:
        before = db.get(Optimizacion, earlier).comparison_snapshot
    assert activate(data) == 1
    assert activate(data) == 0
    with data.sessions() as db:
        assert db.get(OptimizationWorkerJob, earlier).state == "WAITING"
        assert db.get(Optimizacion, earlier).comparison_snapshot == before
    response = accept(client, data, "A", key="historical-wait")
    assert response.status_code == 201 and response.json()["comparison_id"] == earlier


def test_cutover_rolls_back_if_a_2a_owner_is_ambiguous(worker_db):
    data = worker_db
    waiting = accept_2a(data, "A", "historical-wait").comparison_id
    orphan = accept_2a(data, "O", "historical-owner").comparison_id
    with data.uow() as tx:
        assert tx.comparisons_v2.claim_supervisor(orphan, uuid4().hex, process_identity(os.getpid()))
    with pytest.raises(RunError) as failure:
        activate(data)
    assert failure.value.code == "RECOVERY_BLOCKED"
    _, client = data.services.api("api-ambiguous")
    assert queue_state(client, data, "O", orphan) == "RECOVERY_BLOCKED"
    legacy = client.post("/api/optimization/runs", headers=data.headers["A"], json={"order_ids": [data.orders["L"]]})
    assert legacy.status_code == 503 and legacy.json()["detail"]["code"] == "RECOVERY_BLOCKED"
    with data.sessions() as db:
        assert not pilot_enabled(db)
        assert db.get(OptimizationWorkerJob, waiting) is None
        assert db.get(Pedido, data.orders["O"]).estado == "EN_OPTIMIZACION"


def test_hidden_pilot_policy_rejects_legacy_admission(worker_db):
    data = worker_db
    activate(data)
    role = f"qa_restricted_{uuid4().hex}"
    with data.engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
        connection.exec_driver_sql(f'GRANT SELECT, UPDATE ON optimization_worker_policy TO "{role}"')
        connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
        with Session(bind=connection) as db, pytest.raises(RunError) as refused:
            require_legacy_admission(db)
        assert refused.value.code == "RESOURCE_UNAVAILABLE"
        assert refused.value.details["request_accepted"] is False


def operational_attempt(data, comparison, generation):
    with data.sessions() as db:
        return db.get(OptimizationWorkerAttempt, (comparison, generation))


def test_live_stale_owner_blocks_then_pre_dispatch_death_allows_one_new_generation(worker_db):
    data = worker_db
    activate(data)
    _, api = data.services.api("api-generation")
    identifier = accept(api, data, "A").json()["comparison_id"]
    old = data.services.recovery_worker("owner-old", "BEFORE")
    initial = operational_attempt(data, identifier, 1)
    assert initial.owner_identity == process_identity(old.pid)
    old.send_signal(signal.SIGSTOP)
    try:
        next_order = accept(api, data, "O").json()["comparison_id"]
        data.services.worker("owner-new", hold=data.orders["A"])
        wait_for(lambda: queue_state(api, data, "A", identifier), lambda state: state == "RECOVERY_BLOCKED")
        assert queue_state(api, data, "O", next_order) == "WAITING"
        assert process_identity(old.pid) == initial.owner_identity
        assert operational_attempt(data, identifier, 2) is None
    finally:
        old.send_signal(signal.SIGCONT)
    (data.services.path / "owner-old-resume").touch()
    wait_for(lambda: data.services.read_log("owner-old"), lambda value: "V2 supervisor stopped with an error" in value)
    assert queue_state(api, data, "A", identifier) == "RECOVERY_BLOCKED"
    assert not summary(api, data, "A", identifier)["terminal"]
    old.kill()
    old.wait(timeout=5)
    resumed = wait_running(api, data, "A", identifier)
    first, second = [operational_attempt(data, identifier, n) for n in (1, 2)]
    assert first.closure == "NO_DISPATCH" and first.closed_at is not None
    for stale in (WorkerClaim(first.owner_token, 1), WorkerClaim(second.owner_token, 1)):
        with pytest.raises(RunError) as refused:
            with data.uow() as tx:
                tx.comparisons_v2.record_worker(identifier, resumed["executions"][0]["execution_id"], stale, None)
        assert refused.value.code in ("RECOVERY_BLOCKED", "STATE_CONFLICT")
    with pytest.raises(RunError) as unbound:
        with data.uow() as tx:
            current = tx.comparisons_v2.get(identifier)
            ff = current.executions[0]
            payload = ExecutionPayloadV2(method="FF", result=None, diagnostic=ExecutionDiagnostic(
                origin="SUPERVISOR", termination_reason="EXECUTION_FAILED", statistics={}))
            tx.comparisons_v2.save_execution(identifier, ff.execution_id, payload, (), current.version)
    assert unbound.value.code == "RECOVERY_BLOCKED"
    assert accept(api, data, "A").json()["comparison_id"] == identifier
    data.services.resume_file.touch()
    wait_for(lambda: queue_state(api, data, "A", identifier), lambda state: state == "TERMINAL")
    wait_for(lambda: queue_state(api, data, "O", next_order), lambda state: state == "TERMINAL")
    assert operational_attempt(data, identifier, 2).closure == "JOINED"
    assert inventory(data) == (1, 0, 0)


def test_death_after_committed_join_preserves_v4_result_and_closes_without_retry(worker_db):
    data = worker_db
    activate(data)
    _, api = data.services.api("api-after-result")
    identifier = accept(api, data, "A").json()["comparison_id"]
    owner = data.services.recovery_worker("owner-after-result", "AFTER")
    current = summary(api, data, "A", identifier)
    ff = current["executions"][0]
    assert ff["complete"] and not current["terminal"]
    with data.sessions() as db:
        result = db.get(EjecucionOptimizacion, ff["execution_id"]).patron_resultado
        assert result["snapshot"]["snapshot_version"] == 4
    owner.kill()
    owner.wait(timeout=5)
    data.services.worker("owner-recovery")
    wait_for(lambda: queue_state(api, data, "A", identifier), lambda state: state == "TERMINAL")
    closed = summary(api, data, "A", identifier)
    assert closed["supervisor_stopped"] and closed["executions"][0]["eligible"]
    assert {job["diagnostic"]["termination_reason"] for job in closed["executions"][1:]} == {"WORKER_STOPPED"}
    assert operational_attempt(data, identifier, 1).closure == "DURABLE_JOIN"
    assert operational_attempt(data, identifier, 2) is None
    with data.sessions() as db:
        assert db.get(EjecucionOptimizacion, ff["execution_id"]).patron_resultado == result
        assert db.get(Pedido, data.orders["A"]).estado == "OPTIMIZADO"
    assert inventory(data) == (1, 0, 0)


def test_mixed_policy_excludes_v1_and_v2_both_ways_and_cancels_bf(worker_db):
    data = worker_db
    activate(data, mixed=True)
    data.services.stack.callback(data.services.v1_resume.touch)
    _, a = data.services.api("mixed-v1", hold_v1=True)
    data.services.stack.callback(data.services.v1_resume.touch)
    _, b = data.services.api("mixed-v2")
    path = "/api/optimization/comparisons"

    def v1(client, actor, key, order):
        return client.post(path, headers={**data.headers[actor], "Idempotency-Key": key}, json={"order_ids": [order]})

    result = v1(a, "A", "qa-mixed-first", data.orders["A"])
    assert result.status_code == 201, result.text
    v1_id = result.json()["comparison_id"]
    wait_for(data.services.v1_ready.exists, bool)
    assert b.patch("/api/inventory/planchas/1", headers=data.headers["L"], json={"cantidad": 0}).status_code == 409
    assert b.get(f"{path}/{v1_id}", headers=data.headers["O"]).status_code == 404
    assert v1(b, "A", "qa-mixed-first", data.orders["A"]).json()["comparison_id"] == v1_id
    v2_id = accept(b, data, "O").json()["comparison_id"]
    data.services.worker("mixed-worker-a", hold=data.orders["O"], method="BF")
    data.services.worker("mixed-worker-b", hold=data.orders["O"], method="BF")
    busy = v1(b, "O", "qa-other-v1", data.orders["L"])
    assert busy.status_code == 503 and busy.json()["detail"]["code"] == "OPTIMIZER_BUSY"
    assert queue_state(b, data, "O", v2_id) == "WAITING"
    data.services.v1_resume.touch()
    result = wait_for(lambda: b.get(f"{path}/{v1_id}", headers=data.headers["A"]).json(),
                      lambda row: row.get("terminal") is True)
    assert len(result["executions"]) == 3 and all(e["complete"] for e in result["executions"])
    running = wait_for(lambda: summary(b, data, "O", v2_id),
                       lambda row: row["executions"][1]["dispatch_state"] == "RUNNING")
    busy = v1(a, "A", "qa-v2-is-active", data.orders["L"])
    assert busy.status_code == 503 and busy.json()["detail"]["code"] == "OPTIMIZER_BUSY"
    rejected = b.post("/api/optimization/runs", headers=data.headers["O"], json={"order_ids": [data.orders["L"]]})
    assert rejected.status_code == 503 and rejected.json()["detail"]["code"] == "RESOURCE_UNAVAILABLE"
    assert cancellation(a, data, "O", running).status_code == 202
    wait_for(lambda: queue_state(b, data, "O", v2_id), lambda state: state == "TERMINAL")
    closed = summary(b, data, "O", v2_id)
    assert closed["cancellation_status"] == "CANCELLED" and closed["executions"][0]["complete"]
    assert operational_attempt(data, v1_id, 1).closure == "JOINED"
    assert inventory(data) == (1, 0, 0)


def test_upgrade_keeps_2b1_current_and_waiting_records_without_rewriting_snapshots(isolated_postgres):
    with new_database(isolated_postgres) as engine:
        result = upgrade(engine, "head")
        assert result.returncode == 0, result.stderr
        data = prepare_queue_data(engine)
        data.uow = partial(SqlComparisonPersistenceUnitOfWork, data.sessions)
        active = accept_2a(data, "A", "pre-fencing-active").comparison_id
        waiting = accept_2a(data, "O", "pre-fencing-waiting").comparison_id
        token = uuid4().hex
        with data.sessions.begin() as db:
            row = db.get(Optimizacion, active)
            runtime = {**row.comparison_snapshot["runtime"], "owner_scope": process_scope(),
                       "supervisor": {"token": token, "identity": process_identity(os.getpid())}}
            row.comparison_snapshot = {**row.comparison_snapshot, "runtime": runtime}
            db.flush()
            before = [db.get(Optimizacion, identifier).comparison_snapshot for identifier in (active, waiting)]
        reverted = downgrade(engine, "s2conc01b100")
        assert reverted.returncode == 0, reverted.stderr
        with data.sessions.begin() as db:
            db.execute(text("INSERT INTO optimization_worker_jobs (comparison_id, state, slot, owner_token, heartbeat_at) "
                            "VALUES (:id, 'RUNNING', 1, :token, clock_timestamp())"), {"id": active, "token": token})
            db.execute(text("INSERT INTO optimization_worker_jobs (comparison_id, state) VALUES (:id, 'WAITING')"),
                       {"id": waiting})
        result = upgrade(engine, "head")
        assert result.returncode == 0, result.stderr
        with data.uow() as tx:
            job = tx.session.get(OptimizationWorkerJob, active)
            assert (job.generation, job.slot, job.owner_token) == (1, 1, token)
            attempt = tx.session.get(OptimizationWorkerAttempt, (active, 1))
            assert attempt.owner_identity == process_identity(os.getpid()) and attempt.closed_at is None
            assert tx.session.get(OptimizationWorkerJob, waiting).generation == 0
            assert tx.comparisons_v2.get(waiting).waiting
            assert [tx.session.get(Optimizacion, identifier).comparison_snapshot for identifier in (active, waiting)] == before


RUNS = "/api/optimization/runs"
FF = f"{RUNS}/durable"
V1 = "/api/optimization/comparisons"


def accept_ff(client, data, actor, *, order=None, key="qa-durable-ff"):
    return client.post(FF, headers={**data.headers[actor], "Idempotency-Key": key},
                       json={"order_ids": [order or data.orders[actor]]})


def ff_state(client, data, actor, identifier):
    response = client.get(f"{FF}/{identifier}", headers=data.headers[actor])
    assert response.status_code == 200, response.text
    return response.json()["state"]


def ff_dispatched(data, identifier):
    attempt = operational_attempt(data, identifier, 1)
    return attempt if attempt and attempt.worker_identity else None


def test_durable_ff_owns_the_shared_slot_across_api_restart_and_concurrent_confirmation(worker_db, monkeypatch):
    data = worker_db
    activate(data, mixed=True)
    api_a, a = data.services.api("ff-api-a")
    api_b, b = data.services.api("ff-api-b")
    assert a.post(FF, headers=data.headers["A"], json={"order_ids": [data.orders["A"]]}).status_code == 422
    assert accept_ff(b, data, "L").status_code == 403
    assert b.get("/api/inventory/planchas", headers=data.headers["L"]).status_code == 200
    with ThreadPoolExecutor(2) as requests:
        results = list(requests.map(lambda api: accept_ff(api, data, "A"), (a, b)))
    assert all(response.status_code == 202 for response in results), [r.text for r in results]
    first = results[0].json()["id_optimizacion"]
    assert all(response.headers["Location"] == f"{FF}/{first}" for response in results)
    assert accept_ff(a, data, "A", order=data.orders["L"]).status_code == 409
    assert accept_ff(b, data, "O", order=data.orders["A"]).status_code == 409
    assert b.get(f"{FF}/{first}", headers=data.headers["O"]).status_code == 404
    assert b.post(f"{RUNS}/{first}/confirm", headers=data.headers["L"]).status_code == 403
    for name in ("ff-worker-a", "ff-worker-b"):
        data.services.worker(name, hold=data.orders["A"])
    owner = wait_for(lambda: ff_dispatched(data, first), bool)
    second = accept(b, data, "O").json()["comparison_id"]
    assert owned_get(a, data, "A", second, "/queue").status_code == 404
    assert queue_state(b, data, "O", second) == "WAITING"
    assert a.post(V1, headers={**data.headers["A"], "Idempotency-Key": "qa-ff-blocks-v1"},
                  json={"order_ids": [data.orders["L"]]}).status_code == 503
    assert a.post(f"{RUNS}/{first}/confirm", headers=data.headers["A"]).status_code == 422
    for process in (api_a, api_b):
        data.services.stop(process)
    _, a = data.services.api("ff-api-a-restarted")
    _, b = data.services.api("ff-api-b-restarted")
    assert accept_ff(b, data, "A").json() == dict(id_optimizacion=first, state="RUNNING", result_url=None)
    assert ff_state(a, data, "A", first) == "RUNNING"
    assert queue_state(b, data, "O", second) == "WAITING"
    with data.sessions() as db:
        assert db.scalars(select(OptimizationWorkerJob.comparison_id).where(OptimizationWorkerJob.slot == 1)).all() == [first]
        assert db.get(OptimizationWorkerAttempt, (first, 1)).owner_token == owner.owner_token
    data.services.resume_file.touch()
    wait_for(lambda: ff_state(b, data, "A", first), lambda state: state == "TERMINAL")
    result = b.get(f"{RUNS}/{first}", headers=data.headers["A"])
    assert result.status_code == 200 and result.json()["completo"], result.text
    assert accept_ff(a, data, "A").json()["result_url"] == f"{RUNS}/{first}"
    wait_for(lambda: queue_state(a, data, "O", second), lambda state: state == "TERMINAL")
    comparison = summary(a, data, "O", second)
    assert len(comparison["executions"]) == 6 and all(row["complete"] for row in comparison["executions"])
    ff_attempt = operational_attempt(data, first, 1)
    assert ff_attempt.closure == "JOINED" and ff_attempt.closed_at <= operational_attempt(data, second, 1).started_at
    assert operational_attempt(data, first, 2) is None
    assert inventory(data) == (1, 0, 0)
    print(next(line for name in ("ff-worker-a", "ff-worker-b")
               for line in data.services.read_log(name).splitlines() if f"worker_ff_returned id={first} " in line))

    from app.modules.optimization.application.confirmation import ConfirmRun
    from app.modules.optimization.infrastructure.confirmation_adapter import SqlConfirmationRepository

    def interrupt_confirmation(*args):
        raise RuntimeError("Test interruption after stock consumption")

    with monkeypatch.context() as fault:
        fault.setattr(SqlConfirmationRepository, "mark_confirmed", interrupt_confirmation)
        with pytest.raises(RuntimeError, match="Test interruption"):
            ConfirmRun(data.uow).execute(first, data.actors["A"])
    assert inventory(data) == (1, 0, 0)

    selection = a.put(f"{BASE}/{second}/selection", headers=data.headers["O"],
        json={"execution_id": comparison["executions"][0]["execution_id"], "expected_version": comparison["version"]})
    assert selection.status_code == 200, selection.text
    selected = selection.json()
    with ThreadPoolExecutor(2) as requests:
        confirm_ff = requests.submit(a.post, f"{RUNS}/{first}/confirm", headers=data.headers["A"])
        confirm_v2 = requests.submit(b.post, f"{BASE}/{second}/confirm",
            headers={**data.headers["O"], "Idempotency-Key": "qa-ff-v2-confirm"},
            json={"execution_id": selected["selected_execution_id"], "expected_version": selected["version"]})
        responses = [confirm_ff.result(), confirm_v2.result()]
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    assert inventory(data) == (0, 1, 1)
    if responses[0].status_code == 200:
        repeat = b.post(f"{RUNS}/{first}/confirm", headers=data.headers["A"])
        assert repeat.status_code == 409 and repeat.json()["detail"]["code"] == "ALREADY_CONFIRMED"
    else:
        repeat = a.post(f"{BASE}/{second}/confirm",
            headers={**data.headers["O"], "Idempotency-Key": "qa-ff-v2-confirm"},
            json={"execution_id": selected["selected_execution_id"], "expected_version": selected["version"]})
        assert repeat.status_code == 200 and repeat.json() == responses[1].json()
    assert inventory(data) == (0, 1, 1)


def test_ff_waits_during_v2_and_uses_opportunity_after_verified_close(worker_db):
    data = worker_db
    activate(data, mixed=True)
    _, a = data.services.api("reverse-api-a")
    _, b = data.services.api("reverse-api-b")
    first = accept(a, data, "A").json()["comparison_id"]
    data.services.worker("reverse-worker", hold=data.orders["A"])
    wait_running(a, data, "A", first)
    response = accept_ff(b, data, "O")
    assert response.status_code == 202, response.text
    second = response.json()["id_optimizacion"]
    assert ff_state(a, data, "O", second) == "WAITING"
    assert a.get(f"{RUNS}/{second}", headers=data.headers["A"]).status_code == 404
    assert operational_attempt(data, second, 1) is None
    data.services.resume_file.touch()
    wait_for(lambda: ff_state(b, data, "O", second), lambda state: state == "TERMINAL")
    assert operational_attempt(data, first, 1).closed_at <= operational_attempt(data, second, 1).started_at
    assert b.get(f"{RUNS}/{second}", headers=data.headers["O"]).json()["completo"]
    assert inventory(data) == (1, 0, 0)


def test_ff_waits_during_v1_and_checks_permissions_at_claim(worker_db):
    data = worker_db
    activate(data, mixed=True)
    data.services.stack.callback(data.services.v1_resume.touch)
    _, a = data.services.api("ff-v1-active", hold_v1=True)
    _, b = data.services.api("ff-v1-client")
    result = a.post(V1, headers={**data.headers["A"], "Idempotency-Key": "qa-v1-blocks-ff"},
                    json={"order_ids": [data.orders["A"]]})
    assert result.status_code == 201, result.text
    wait_for(data.services.v1_ready.exists, bool)
    second = accept_ff(b, data, "O").json()["id_optimizacion"]
    data.services.worker("ff-v1-worker")
    assert ff_state(a, data, "O", second) == "WAITING"
    with data.sessions.begin() as db:
        db.execute(delete(RolPermiso).where(RolPermiso.id_rol == data.role_ids["O"],
                                           RolPermiso.id_permiso == data.codes["optimization.execute"]))
    assert accept_ff(b, data, "O").status_code == 403
    data.services.v1_resume.touch()
    wait_for(lambda: ff_state(a, data, "O", second), lambda state: state == "TERMINAL")
    assert operational_attempt(data, second, 1) is None
    with data.sessions() as db:
        assert db.get(Pedido, data.orders["O"]).estado == "PENDIENTE"
        assert db.scalar(select(EjecucionOptimizacion).where(EjecucionOptimizacion.id_optimizacion == second)
                         ).patron_resultado["failure_code"] == "PERMISSION_REVOKED"
    assert inventory(data) == (1, 0, 0)


def test_ff_recovers_once_before_dispatch_and_fences_old_generation(worker_db):
    data = worker_db
    activate(data, mixed=True)
    _, api = data.services.api("ff-generation-api")
    identifier = accept_ff(api, data, "A").json()["id_optimizacion"]
    old = data.services.recovery_worker("ff-owner-old", "FF_BEFORE")
    initial = operational_attempt(data, identifier, 1)
    assert initial.owner_identity == process_identity(old.pid) and initial.dispatch_at is None
    old.send_signal(signal.SIGSTOP)
    try:
        with data.sessions.begin() as db:
            db.execute(text("UPDATE optimization_worker_jobs SET heartbeat_at = clock_timestamp() - interval '11 seconds' "
                            "WHERE comparison_id = :id"), {"id": identifier})
        data.services.worker("ff-owner-new", hold=data.orders["A"])
        next_id = accept(api, data, "O").json()["comparison_id"]
        wait_for(lambda: ff_state(api, data, "A", identifier), lambda state: state == "RECOVERY_BLOCKED")
        assert queue_state(api, data, "O", next_id) == "WAITING"
        assert operational_attempt(data, identifier, 2) is None
    finally:
        old.send_signal(signal.SIGCONT)
    (data.services.path / "ff-owner-old-resume").touch()
    wait_for(lambda: data.services.read_log("ff-owner-old"), lambda log: "worker_ff_deferred" in log)
    assert ff_state(api, data, "A", identifier) == "RECOVERY_BLOCKED"
    old.kill()
    old.wait(timeout=5)
    second = wait_for(lambda: operational_attempt(data, identifier, 2), lambda attempt: attempt and attempt.worker_identity)
    assert operational_attempt(data, identifier, 1).closure == "NO_DISPATCH"
    for claim in (None, WorkerClaim(initial.owner_token, 1), WorkerClaim(second.owner_token, 1)):
        with pytest.raises(RunError) as denied:
            with data.uow() as tx:
                tx.runs.fail(identifier, "OBSOLETE", claim=claim)
        assert denied.value.code == "RECOVERY_BLOCKED"
        with pytest.raises(RunError) as denied:
            with data.uow() as tx:
                result = tx.runs.get(identifier).result
                tx.runs.save_result(identifier, result, (), claim=claim)
        assert denied.value.code == "RECOVERY_BLOCKED"
    data.services.resume_file.touch()
    wait_for(lambda: ff_state(api, data, "A", identifier), lambda state: state == "TERMINAL")
    wait_for(lambda: queue_state(api, data, "O", next_id), lambda state: state == "TERMINAL")
    assert accept_ff(api, data, "A").json()["id_optimizacion"] == identifier
    assert operational_attempt(data, identifier, 2).closure == "JOINED"
    assert inventory(data) == (1, 0, 0)


def test_ff_crash_after_dispatch_keeps_order_and_slot_blocked(worker_db):
    data = worker_db
    activate(data, mixed=True)
    _, api = data.services.api("ff-crash-api")
    identifier = accept_ff(api, data, "A").json()["id_optimizacion"]
    pending = accept(api, data, "O").json()["comparison_id"]
    owner = data.services.worker("ff-crash-owner", hold=data.orders["A"])
    attempt = wait_for(lambda: ff_dispatched(data, identifier), bool)
    assert process_identity(attempt.worker_identity["pid"]) == attempt.worker_identity
    owner.kill()
    owner.wait(timeout=5)
    with data.sessions.begin() as db:
        db.execute(text("UPDATE optimization_worker_jobs SET heartbeat_at = clock_timestamp() - interval '11 seconds' "
                        "WHERE comparison_id = :id"), {"id": identifier})
    data.services.worker("ff-crash-candidate")

    def blocked_job():
        with data.sessions() as db:
            row = db.get(OptimizationWorkerJob, identifier)
            return row if row.state == "RECOVERY_BLOCKED" else None

    blocked = wait_for(blocked_job, bool)
    assert (blocked.slot, blocked.generation) == (1, 1)
    assert accept_ff(api, data, "A").json()["state"] == "RECOVERY_BLOCKED"
    assert queue_state(api, data, "O", pending) == "WAITING"
    assert operational_attempt(data, identifier, 2) is None
    with pytest.raises(RunError) as denied:
        with data.uow() as tx:
            tx.runs.recover_stopped(identifier)
    assert denied.value.code == "RECOVERY_BLOCKED"
    with data.sessions() as db:
        assert db.get(Pedido, data.orders["A"]).estado == "EN_OPTIMIZACION"
    assert inventory(data) == (1, 0, 0)


def test_ff_worker_death_after_atomic_result_preserves_result_and_frees_slot(worker_db):
    data = worker_db
    activate(data, mixed=True)
    _, api = data.services.api("ff-after-api")
    identifier = accept_ff(api, data, "A").json()["id_optimizacion"]
    owner = data.services.recovery_worker("ff-after-owner", "FF_AFTER")
    assert ff_state(api, data, "A", identifier) == "TERMINAL"
    before = api.get(f"{RUNS}/{identifier}", headers=data.headers["A"]).json()
    assert before["completo"] and operational_attempt(data, identifier, 1).closure == "JOINED"
    owner.kill()
    owner.wait(timeout=5)
    second = accept(api, data, "O").json()["comparison_id"]
    data.services.worker("ff-after-candidate")
    wait_for(lambda: queue_state(api, data, "O", second), lambda state: state == "TERMINAL")
    assert api.get(f"{RUNS}/{identifier}", headers=data.headers["A"]).json() == before
    assert operational_attempt(data, identifier, 2) is None
    reversed_revision = downgrade(data.engine, "s2conc01b200")
    assert reversed_revision.returncode != 0 and "Keep progress cursors and open stock protections" in reversed_revision.stderr
    assert inventory(data) == (1, 0, 0)


def test_ff_transient_postgres_outage_waits_for_join_before_releasing_the_slot(worker_db):
    data = worker_db
    activate(data, mixed=True)
    _, api = data.services.api("ff-outage-api")
    identifier = accept_ff(api, data, "A").json()["id_optimizacion"]
    pending = accept(api, data, "O").json()["comparison_id"]
    process = data.services.worker("ff-outage-owner", hold=data.orders["A"], pause_ff_started_event=True)
    wait_for((data.services.path / "ff-outage-owner-ready").exists, bool)
    admin = create_engine(data.cluster, isolation_level="AUTOCOMMIT")
    database = data.engine.url.database
    try:
        with admin.connect() as control:
            assert database.startswith("r1_") and database[3:].isalnum()
            control.exec_driver_sql(f'ALTER DATABASE "{database}" WITH ALLOW_CONNECTIONS false')
            try:
                control.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                     "WHERE datname = :database AND application_name = 'ff-outage-owner'"),
                                {"database": database})
                wait_for(lambda: data.services.read_log("ff-outage-owner"),
                         lambda log: "worker_v2_deferred reason=OperationalError" in log, seconds=6)
                data.services.resume_file.touch()
                wait_for(lambda: data.services.read_log("ff-outage-owner"),
                         lambda log: "operation=_persist_started" in log, seconds=3)
                assert process.poll() is None
            finally:
                control.exec_driver_sql(f'ALTER DATABASE "{database}" WITH ALLOW_CONNECTIONS true')
    finally:
        admin.dispose()
    wait_for(lambda: ff_state(api, data, "A", identifier), lambda state: state == "TERMINAL")
    wait_for(lambda: queue_state(api, data, "O", pending), lambda state: state == "TERMINAL")
    attempt = operational_attempt(data, identifier, 1)
    assert attempt.closure == "JOINED" and attempt.closed_at <= operational_attempt(data, pending, 1).started_at
    result = api.get(f"{RUNS}/{identifier}", headers=data.headers["A"]).json()
    assert result["estado"] == "FALLIDA" and operational_attempt(data, identifier, 2) is None
    progress = api.get(f"{FF}/{identifier}/progress", headers=data.headers["A"]).json()
    assert [event["kind"] for event in progress["events"]] == ["WAITING", "RUNNING", "ENGINE_STARTED", "ENGINE_FINISHED", "TERMINAL"]
    assert inventory(data) == (1, 0, 0)


def test_ff_and_v2_share_the_last_waiting_place(worker_db):
    data = worker_db
    activate(data, mixed=True)
    data.services.environment["NEWGLASS_COMPARISON_QUEUE_CAPACITY"] = "1"
    _, a = data.services.api("ff-bounded-api-a")
    _, b = data.services.api("ff-bounded-api-b")
    with ThreadPoolExecutor(2) as requests:
        ff = requests.submit(accept_ff, a, data, "A")
        v2 = requests.submit(accept, b, data, "O")
        responses = (ff.result(), v2.result())
    assert tuple(r.status_code for r in responses) in ((202, 503), (503, 201)), [r.text for r in responses]
    rejected = next(r for r in responses if r.status_code == 503)
    assert rejected.json()["detail"]["request_accepted"] is False
    if responses[0].status_code == 202:
        assert accept_ff(b, data, "A").json() == responses[0].json()
    else:
        assert accept(a, data, "O").json()["comparison_id"] == responses[1].json()["comparison_id"]
    with data.uow() as tx:
        assert tx.worker_jobs.waiting_count() == 1 and not tx.worker_jobs.occupied()
        loser = "A" if rejected is responses[0] else "O"
        assert tx.session.get(Pedido, data.orders[loser]).estado == "PENDIENTE"
    assert inventory(data) == (1, 0, 0)
