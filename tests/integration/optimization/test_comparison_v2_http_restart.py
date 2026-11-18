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

from app.models import EjecucionOptimizacion, Optimizacion, OptimizationWorkerJob, Pedido, RolPermiso
from app.modules.optimization.application.comparison_acceptance_v2 import AcceptComparisonV2
from app.modules.optimization.application.errors import RunError
from app.modules.optimization.infrastructure.comparison_v2_uow import SqlComparisonPersistenceUnitOfWork
from app.modules.optimization.infrastructure.process_lifecycle import process_identity
from app.modules.optimization.infrastructure.worker_jobs import enable_pilot, pilot_enabled
from app.modules.optimization.presentation.comparison_supervision_v2 import authorize_actor
from tests.integration.postgres_support import BACKEND, new_database, upgrade
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

    def api(self, name):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
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

    def worker(self, name, *, hold=None):
        if hold is None:
            args, environment = ["-m", "app.modules.optimization.worker_v2"], {}
        else:
            args = ["-c", "from tests.e2e_cancellation_worker import run_gated_worker; run_gated_worker()"]
            environment = {"QA_HOLD_ORDER": str(hold), "QA_RESUME_ENGINE": str(self.resume_file)}
        process = self.start(name, args, **environment)
        self.workers[process.pid] = process
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


def activate(data):
    with data.uow() as tx:
        return enable_pilot(tx)


def test_two_apis_two_reclaimers_and_api_restart_do_not_interrupt_calculation(worker_db):
    data = worker_db
    assert activate(data) == 0
    a_server, api_a = data.services.api("api-a")
    _, api_b = data.services.api("api-b")
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
    with pytest.raises(IntegrityError):
        with data.sessions.begin() as db:
            contender = db.get(OptimizationWorkerJob, ids[second])
            contender.state, contender.slot, contender.owner_token = "RUNNING", 1, uuid4().hex
            contender.heartbeat_at = datetime.now(timezone.utc)

    data.services.stop(a_server)
    _, restarted = data.services.api("api-a-restarted")
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
    assert queue_state(client, data, "O", second) == "WAITING"
    assert summary(client, data, "A", first)["terminal"] is False
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
