"""Two real Linux workers under one cgroup, with a disposable local database."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
from statistics import quantiles
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import Configuracion, MovimientoInventario, OptimizationWorkerJob, OptimizationWorkerSlot, Plancha
from app.modules.optimization.application.errors import RunError
from app.modules.optimization.infrastructure.parallel_lab import ParallelLab
from app.modules.optimization.infrastructure.worker_jobs import WorkerClaim, configure_lab_capacity
from app.modules.orders.infrastructure.repositories import SQLAlchemyOrderRepository
from tests.integration.postgres_support import BACKEND
from .test_confirmation_rollback import failing_trigger
from .test_comparison_v2_http_restart import (BASE, accept, activate, cancellation, operational_attempt,
    queue_state, summary, wait_for, worker_db)  # noqa: F401

LAB = pytest.mark.skipif(os.environ.get("NEWGLASS_TEST_PARALLEL") != "1", reason="Requires an isolated Linux systemd user lab")
INV = "/api/inventory/planchas"


def test_default_capacity_is_one_and_slot_two_cannot_be_overallocated(worker_db):
    data = worker_db
    activate(data)
    _, client = data.services.api("default-capacity")
    identifier = accept(client, data, "A").json()["comparison_id"]
    with data.sessions() as db:
        assert db.scalars(select(OptimizationWorkerSlot.id)).all() == [1]
    with pytest.raises(IntegrityError) as refused:
        with data.sessions.begin() as db:
            job = db.get(OptimizationWorkerJob, identifier)
            job.slot, job.owner_token, job.generation = 2, uuid4().hex, 1
            job.state, job.heartbeat_at = "RUNNING", datetime.now(timezone.utc)
    assert refused.value.orig.diag.constraint_name == "fk_optimization_worker_enabled_slot"
    assert queue_state(client, data, "A", identifier) == "WAITING"


def systemd(*args):
    result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@contextmanager
def isolated_slice(data, capacity):
    suffix = uuid4().hex[:10]
    name = f"newglasslab-{suffix}.slice"
    anchor = f"newglass-anchor-{suffix}.service"
    subprocess.run(["systemd-run", "--user", "--quiet", f"--unit={anchor}", f"--slice={name}",
                    "/usr/bin/sleep", "1200"], check=True, timeout=15)
    workers = []
    try:
        systemd("set-property", "--runtime", name, "MemoryMax=1700M", "MemorySwapMax=0", "CPUQuota=200%", "TasksMax=64")
        location = systemd("show", name, "-p", "ControlGroup", "--value")
        path = Path("/sys/fs/cgroup") / Path(location).relative_to("/")
        lab = ParallelLab(name, path, ParallelLab.attest(path, Path(location)))
        if capacity == 2:
            result = subprocess.run(["systemd-run", "--user", "--scope", "--quiet", f"--slice={name}",
                sys.executable, "-B", "-m", "app.modules.optimization.worker_v2", "--lab-capacity", "2", "--lab-slice", name],
                cwd=BACKEND, env=data.services.environment, capture_output=True, text=True, timeout=25)
            assert result.returncode == 0, result.stderr
        def start(number, folder, *, partners=(), holds=()):
            unit = f"newglass-worker-{suffix}-{number}.scope"
            log = data.services.path / f"lab-worker-{number}.log"
            with log.open("w", encoding="utf-8") as output:
                worker = subprocess.Popen(["systemd-run", "--user", "--scope", "--quiet", f"--unit={unit}",
                    f"--slice={name}", sys.executable, "-B", "-m", "tests.e2e_parallel_worker", "--lab-worker", str(number),
                    "--lab-slice", name], cwd=BACKEND, env={**data.services.environment, "PGAPPNAME": f"lab-worker-{number}",
                    "QA_LAB_FILES": str(folder), "QA_LAB_PARTNERS": ",".join(map(str, partners)),
                    "QA_LAB_HOLDS": ",".join(map(str, holds))}, stdout=output, stderr=subprocess.STDOUT)
            workers.append(worker)
            def ready():
                content = log.read_text(encoding="utf-8")
                assert worker.poll() is None, content
                return "worker_v2_ready" in content
            wait_for(ready, bool, seconds=25)
            return worker
        yield lab, start
    finally:
        systemd("stop", name)
        for worker in workers:
            worker.wait(timeout=15)
        systemd("revert", name)


def orders_for(data, count):
    with data.sessions.begin() as db:
        stock = db.get(Plancha, 1)
        stock.ancho_mm, stock.alto_mm, stock.cantidad = 600, 450, 4
        db.scalar(select(Configuracion)).resolucion_raster_mm = 5
        db.execute(text("SELECT setval(pg_get_serial_sequence('planchas', 'id_plancha'), 1)"))
    rect = lambda w, h: dict(type="RECTANGULO", width_mm=w, height_mm=h)
    triangle = dict(type="POLIGONO_CONVEXO", vertices_mm=[[0, 0], [65, 0], [0, 55]])
    def piece(geometry, quantity, area):
        return dict(tipo_forma=geometry["type"], cantidad=quantity, dimensiones=geometry, geometria=geometry,
                    id_tipo_vidrio=7, espesor_mm=6, area_mm2=area)
    shapes = [piece(rect(120, 55), count // 3, 6600), piece(rect(85, 70), count // 3, 5950),
              piece(triangle, count - 2 * (count // 3), 1787.5)]
    with data.sessions() as db:
        repo = SQLAlchemyOrderRepository(db)
        return [repo.create_order(data.actors[actor], shapes, id_cliente=1) for actor in ("A", "O", "A")]


def start_pair(a, b, data, orders):
    with ThreadPoolExecutor(2) as workers:
        jobs = [workers.submit(accept, client, data, actor, order=order, key=f"qa-lab-pair-{actor}")
                for client, actor, order in zip((a, b), ("A", "O"), orders)]
        responses = [job.result() for job in jobs]
    assert all(r.status_code == 201 for r in responses), [r.text for r in responses]
    return [r.json()["comparison_id"] for r in responses]


def motor(folder, order):
    path = folder / f"motor-{order}.json"
    return json.loads(path.read_text()) if path.exists() else None


def progress(client, data, actor, identifier):
    response = client.get(f"{BASE}/{identifier}/progress", headers=data.headers[actor])
    assert response.status_code == 200, response.text
    return response.json()


def assert_overlap(motors):
    first, second = motors
    overlap_ns = min(a["end_ns"] for a in motors) - max(a["start_ns"] for a in motors)
    union_ns = max(a["end_ns"] for a in motors) - min(a["start_ns"] for a in motors)
    assert first["pid"] != second["pid"] and overlap_ns > 0, motors
    # CPU time only surrounds the native optimizer, excluding every test barrier.
    assert sum(a["cpu_ns"] for a in motors) > union_ns, motors
    return dict(overlap_ms=overlap_ns / 1e6, motors=motors)


def timing(values):
    cuts = quantiles(values, n=100, method="inclusive")
    return dict(samples=len(values), p50_ms=cuts[49], p95_ms=cuts[94], p99_ms=cuts[98])


@LAB
@pytest.mark.parametrize("pieces", [20, 40, 100])
@pytest.mark.parametrize("capacity", [1, 2])
def test_same_load_with_one_and_two_slots(worker_db, tmp_path, capacity, pieces):
    data = worker_db
    activate(data)
    orders = orders_for(data, pieces)
    _, a = data.services.api("bench-api-a")
    _, b = data.services.api("bench-api-b")
    warehouse = data.services.stack.enter_context(httpx.Client(base_url=b.base_url, timeout=8))
    with isolated_slice(data, capacity) as (lab, start):
        for number in (1, 2):
            start(number, tmp_path)
        began = time.monotonic()
        identifiers = start_pair(a, b, data, orders)

        def measure(client, actor, identifier):
            values, states = [], set()
            for _ in range(60):
                start_at = time.perf_counter()
                states.add(progress(client, data, actor, identifier)["state"])
                values.append((time.perf_counter() - start_at) * 1000)
                time.sleep(.045)
            return values, states

        def measure_warehouse():
            values = []
            for _ in range(120):
                start_at = time.perf_counter()
                assert warehouse.get(INV, headers=data.headers["L"]).status_code == 200
                values.append((time.perf_counter() - start_at) * 1000)
                time.sleep(.025)
            return values

        samples = []
        expected = list(zip(("A", "O"), identifiers))
        with ThreadPoolExecutor(3) as queries:
            p1 = queries.submit(measure, a, *expected[0])
            p2 = queries.submit(measure, b, *expected[1])
            p3 = queries.submit(measure_warehouse)
            def finished():
                snapshot = lab.sample(include_processes=True)
                with data.sessions() as db:
                    rows = db.execute(select(OptimizationWorkerJob.state, OptimizationWorkerJob.slot)
                                      .where(OptimizationWorkerJob.comparison_id.in_(identifiers))).all()
                    snapshot["active"] = sum(slot is not None for _, slot in rows)
                    snapshot["worker_connections"] = db.scalar(text("SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname=current_database() AND application_name LIKE 'lab-worker-%'"))
                    snapshot["api_connections"] = db.scalar(text("SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname=current_database() AND application_name LIKE 'bench-api-%'"))
                samples.append(snapshot)
                return len(rows) == 2 and all(state == "TERMINAL" for state, _ in rows)

            wait_for(finished, bool, seconds=150)
            paired_wall = (time.monotonic() - began) * 1000
            (a_lat, a_state), (b_lat, b_state) = p1.result(), p2.result()
            inventory_latency = p3.result()
        jobs = []
        for actor, identifier in expected:
            result = summary(a, data, actor, identifier)
            attempt = operational_attempt(data, identifier, 1)
            events = progress(a, data, actor, identifier)["events"]
            accepted_at = datetime.fromisoformat(events[0]["occurred_at"])
            jobs.append(dict(id=identifier, queue_ms=(attempt.started_at - accepted_at).total_seconds()*1000,
                run_ms=(attempt.closed_at - attempt.started_at).total_seconds()*1000, state=result["status"],
                engines=[dict(method=e["method"], complete=e["complete"], reason=e["diagnostic"]["termination_reason"])
                         for e in result["executions"]]))
        peak = {key: max(s[key] for s in samples) for key in ("memory_peak", "group_rss_bytes", "largest_process_rss",
            "pids_current", "group_processes", "group_cpu_throttled_us", "group_cpu_usage_us", "active",
            "worker_connections", "api_connections", "oom", "oom_kill", "host_cpu_pressure", "group_cpu_pressure")}
        peak["min_host_available"] = min(s["host_available"] for s in samples)
        assert peak["worker_connections"] <= 4 and peak["active"] <= capacity
        report = dict(capacity=capacity, pieces=pieces, wall_ms=paired_wall, jobs=jobs, peak=peak,
                      limits=lab.profile, progress=timing(a_lat+b_lat), inventory=timing(inventory_latency),
                      observed_states=sorted(a_state|b_state), ff=[motor(tmp_path, order) for order in orders[:2]],
                      worker_logs=[line.split("worker_v2_closed ", 1)[1]
                          for number in (1, 2) for line in data.services.read_log(f"lab-worker-{number}").splitlines()
                          if "worker_v2_closed " in line])
        if capacity == 1:
            ff_a, ff_b = report["ff"]
            assert max(ff_a["start_ns"], ff_b["start_ns"]) >= min(ff_a["end_ns"], ff_b["end_ns"])
        with data.sessions() as db:
            assert db.get(Plancha, 1).cantidad == 4
            assert db.scalar(select(func.count()).select_from(MovimientoInventario)) == 0
        print("parallel_benchmark " + json.dumps(report))


@LAB
def test_two_real_engines_and_three_users_cancel_and_handoff(worker_db, tmp_path):
    data = worker_db
    activate(data, mixed=True)
    orders = orders_for(data, 20)
    process_a, a = data.services.api("parallel-api-a")
    process_b, b = data.services.api("parallel-api-b")
    warehouse = data.services.stack.enter_context(httpx.Client(base_url=b.base_url, timeout=8))
    identifiers = start_pair(a, b, data, orders)
    assert accept(b, data, "O", order=orders[0], key="qa-lab-same-order").status_code == 409
    with isolated_slice(data, 2) as (lab, start):
        for number in (1, 2):
            start(number, tmp_path, partners=orders[:2], holds=orders[:2])
        # Three HTTP sessions operate together while two motors are owned by independent workers.
        wait_for(lambda: [motor(tmp_path, order) for order in orders[:2]], all, seconds=60)
        samples = assert_overlap([motor(tmp_path, order) for order in orders[:2]])
        assert all(m["placed"] == m["total"] == 20 for m in samples["motors"])
        attempts = [operational_attempt(data, id_, 1) for id_ in identifiers]
        assert attempts[0].owner_identity["pid"] != attempts[1].owner_identity["pid"]
        with data.sessions() as db:
            assert {db.get(OptimizationWorkerJob, id_).slot for id_ in identifiers} == {1, 2}
        # Neither configuration downgrade nor incompatible stock change may race the held slots.
        with pytest.raises(RunError, match="cierre"):
            with data.uow() as tx:
                configure_lab_capacity(tx, None)
        c = accept(a, data, "A", order=orders[2], key="qa-lab-third").json()["comparison_id"]
        assert queue_state(b, data, "A", c) == "WAITING"
        with ThreadPoolExecutor(3) as queries:
            tasks = [queries.submit(progress, b, data, "A", identifiers[0]),
                     queries.submit(progress, a, data, "O", identifiers[1]),
                     queries.submit(warehouse.post, INV, headers=data.headers["L"],
                         json=dict(ancho_mm=700, alto_mm=500, espesor_mm=6, cantidad=2, id_tipo_vidrio=7))]
            result_a, result_b, added = [task.result() for task in tasks]
        assert result_a["state"] == result_b["state"] == "RUNNING" and added.status_code == 201, added.text
        samples["warehouse_create_ms"] = added.elapsed.total_seconds() * 1000
        material = added.json()["id_plancha"]
        updated = warehouse.patch(f"{INV}/{material}", headers=data.headers["L"], json={"cantidad": 3})
        assert updated.status_code == 200, updated.text
        samples["warehouse_update_ms"] = updated.elapsed.total_seconds() * 1000
        assert warehouse.get(f"{INV}/{material}/movimientos", headers=data.headers["L"]).json() == []
        assert warehouse.patch(f"{INV}/1", headers=data.headers["L"], json={"cantidad": 0}).status_code == 409
        assert accept(warehouse, data, "L", order=orders[2]).status_code == 403
        assert b.get(f"{BASE}/{identifiers[0]}/progress", headers=data.headers["O"]).status_code == 404
        assert cancellation(b, data, "O", summary(a, data, "A", identifiers[0])).status_code == 404
        assert queue_state(b, data, "A", c) == "WAITING"
        assert b.post("/api/optimization/comparisons", headers={**data.headers["A"], "Idempotency-Key": "qa-lab-v1-busy"},
                      json={"order_ids": [data.orders["L"]]}).status_code == 503
        for server in (process_a, process_b):
            data.services.stop(server)
        _, a = data.services.api("parallel-api-a-restarted")
        _, b = data.services.api("parallel-api-b-restarted")
        assert progress(b, data, "A", identifiers[0])["state"] == "RUNNING"
        assert progress(a, data, "O", identifiers[1])["state"] == "RUNNING"
        cancelled_at = time.monotonic()
        assert cancellation(a, data, "A", summary(b, data, "A", identifiers[0])).status_code == 202
        wait_for(lambda: queue_state(b, data, "A", identifiers[0]), lambda state: state == "TERMINAL")
        samples["cancel_to_close_ms"] = (time.monotonic() - cancelled_at) * 1000
        assert summary(b, data, "A", identifiers[0])["cancellation_status"] == "CANCELLED"
        assert progress(a, data, "O", identifiers[1])["state"] == "RUNNING"
        next_attempt = wait_for(lambda: operational_attempt(data, c, 1), bool)
        assert operational_attempt(data, identifiers[0], 1).closed_at <= next_attempt.started_at
        (tmp_path / f"resume-{orders[1]}").touch()
        for actor, identifier in [("O", identifiers[1]), ("A", c)]:
            wait_for(lambda: queue_state(b, data, actor, identifier), lambda state: state == "TERMINAL", seconds=120)
            result = summary(a, data, actor, identifier)
            assert len(result["executions"]) == 6 and all(item["complete"] for item in result["executions"]), result
        with data.sessions() as db:
            assert db.get(Plancha, 1).cantidad == 4 and db.get(Plancha, material).cantidad == 3
            assert db.scalar(select(func.count()).select_from(MovimientoInventario)) == 0
        assert b.patch(f"{INV}/1", headers=data.headers["L"], json={"cantidad": 1}).status_code == 200

        def select_ff(actor, identifier):
            current = summary(b, data, actor, identifier)
            execution = current["executions"][0]
            assert execution["metrics"]["planchas_nuevas_usadas"] == 1
            response = b.put(f"{BASE}/{identifier}/selection", headers=data.headers[actor],
                json=dict(execution_id=execution["execution_id"], expected_version=current["version"]))
            assert response.status_code == 200, response.text
            return dict(execution_id=execution["execution_id"], expected_version=response.json()["version"])

        selected = [("O", identifiers[1], select_ff("O", identifiers[1])), ("A", c, select_ff("A", c))]

        def confirm(client, actor, identifier, body):
            return client.post(f"{BASE}/{identifier}/confirm", json=body,
                headers={**data.headers[actor], "Idempotency-Key": "qa-lab-confirm"})

        assert confirm(b, "L", *selected[0][1:]).status_code == 403
        with failing_trigger(data.sessions, "optimizaciones", "UPDATE", "NEW.comparison_snapshot ? 'confirmation'"):
            assert confirm(a, *selected[0]).status_code == 500
        with data.sessions() as db:
            assert db.get(Plancha, 1).cantidad == 1
            assert db.scalar(select(func.count()).select_from(MovimientoInventario)) == 0
        assert not summary(a, data, "O", identifiers[1])["confirmed"]
        with ThreadPoolExecutor(2) as confirmers:
            competing = [confirmers.submit(confirm, client, *choice) for client, choice in zip((a, b), selected)]
            receipts = [request.result() for request in competing]
        assert sorted(response.status_code for response in receipts) == [200, 409], [r.text for r in receipts]
        winner = next(index for index, response in enumerate(receipts) if response.status_code == 200)
        assert confirm(b, *selected[winner]).json() == receipts[winner].json()
        with data.sessions() as db:
            assert db.get(Plancha, 1).cantidad == 0 and db.get(Plancha, material).cantidad == 3
            movement = db.scalar(select(MovimientoInventario))
            assert (movement.id_optimizacion, movement.id_plancha, movement.cantidad_afectada) == (selected[winner][1], 1, 1)
            assert db.scalar(select(func.count()).select_from(MovimientoInventario)) == 1
        samples["host"] = lab.sample(include_processes=True)
        print("parallel_proof " + json.dumps(samples))


@LAB
def test_blocked_slot_never_reused_while_the_other_keeps_working(worker_db, tmp_path):
    data = worker_db
    activate(data)
    orders = orders_for(data, 20)
    _, api = data.services.api("parallel-recovery-api")
    ids = start_pair(api, api, data, orders)
    with isolated_slice(data, 2) as (_, start):
        for lane in (1, 2):
            start(lane, tmp_path, holds=orders[:2])
        wait_for(lambda: [motor(tmp_path, order) for order in orders[:2]], all, seconds=60)
        attempt = operational_attempt(data, ids[0], 1)
        with data.sessions() as db:
            slot = db.get(OptimizationWorkerJob, ids[0]).slot
        pid = attempt.owner_identity["pid"]
        started = time.monotonic()
        os.kill(pid, signal.SIGSTOP)
        try:
            with data.sessions.begin() as db:
                db.execute(text("UPDATE optimization_worker_jobs SET heartbeat_at=clock_timestamp()-interval '11 seconds' "
                                "WHERE comparison_id=:id"), {"id": ids[0]})
            wait_for(lambda: progress(api, data, "A", ids[0])["state"], lambda state: state == "RECOVERY_BLOCKED")
            assert queue_state(api, data, "O", ids[1]) == "RUNNING"
        finally:
            os.kill(pid, signal.SIGCONT)
        os.kill(pid, signal.SIGKILL)
        c = accept(api, data, "A", order=orders[2], key="qa-surviving-slot").json()["comparison_id"]
        assert queue_state(api, data, "A", c) == "WAITING"
        with pytest.raises(RunError) as fenced:
            with data.uow() as tx:
                tx.worker_jobs.heartbeat(ids[0], WorkerClaim(attempt.owner_token, attempt.generation))
        assert fenced.value.code == "RECOVERY_BLOCKED"
        (tmp_path / f"resume-{orders[1]}").touch()
        wait_for(lambda: operational_attempt(data, c, 1), bool, seconds=100)
        with data.sessions() as db:
            blocked = db.get(OptimizationWorkerJob, ids[0])
            assert blocked.slot == slot and blocked.state == "RECOVERY_BLOCKED"
            assert db.get(OptimizationWorkerJob, c).slot != slot
        print(f"parallel_recovery blocked_slot={slot} surviving_reclaim_ms={(time.monotonic() - started)*1000:.1f}")
        wait_for(lambda: queue_state(api, data, "A", c), lambda state: state == "TERMINAL", seconds=120)
        assert queue_state(api, data, "A", ids[0]) == "RECOVERY_BLOCKED"


@LAB
def test_postgres_interruption_of_one_worker_preserves_the_other_attempt(worker_db, tmp_path):
    data = worker_db
    activate(data)
    orders = orders_for(data, 20)
    _, api = data.services.api("parallel-outage-api")
    a = accept(api, data, "A", order=orders[0], key="qa-lab-outage-a").json()["comparison_id"]
    b = accept(api, data, "O", order=orders[1], key="qa-lab-outage-o").json()["comparison_id"]
    with isolated_slice(data, 2) as (_, start):
        start(1, tmp_path, holds=orders[:2])
        wait_for(lambda: motor(tmp_path, orders[0]), bool, seconds=40)
        start(2, tmp_path, holds=orders[:2])
        wait_for(lambda: motor(tmp_path, orders[1]), bool, seconds=40)
        owner = operational_attempt(data, b, 1).owner_token
        admin = create_engine(data.cluster, isolation_level="AUTOCOMMIT")
        database = data.engine.url.database
        assert database.startswith("r1_") and database[3:].isalnum()
        try:
            with admin.connect() as control:
                control.exec_driver_sql(f'ALTER DATABASE "{database}" WITH ALLOW_CONNECTIONS false')
                try:
                    interrupted = control.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:db AND application_name='lab-worker-1'"), {"db": database}).scalars().all()
                    assert any(interrupted)
                    wait_for(lambda: data.services.read_log("lab-worker-1"),
                             lambda log: "worker_v2_deferred reason=OperationalError" in log, seconds=5)
                finally:
                    control.exec_driver_sql(f'ALTER DATABASE "{database}" WITH ALLOW_CONNECTIONS true')
        finally:
            admin.dispose()
        restored = time.monotonic()
        ending = wait_for(lambda: queue_state(api, data, "A", a),
                          lambda state: state in ("TERMINAL", "RECOVERY_BLOCKED"), seconds=30)
        assert progress(api, data, "O", b)["state"] == "RUNNING"
        assert operational_attempt(data, b, 1).owner_token == owner
        assert operational_attempt(data, a, 2) is None
        (tmp_path / f"resume-{orders[1]}").touch()
        wait_for(lambda: queue_state(api, data, "O", b), lambda state: state == "TERMINAL", seconds=90)
        assert all(item["complete"] for item in summary(api, data, "O", b)["executions"])
        with data.sessions() as db:
            assert db.get(Plancha, 1).cantidad == 4
            assert db.scalar(select(func.count()).select_from(MovimientoInventario)) == 0
        print(f"parallel_postgres_outage affected_state={ending} other_owner_unchanged=true "
              f"restore_to_other_close_ms={(time.monotonic()-restored)*1000:.1f}")


@LAB
def test_near_memory_ceiling_defers_claims_and_recovers_without_oom(worker_db, tmp_path):
    data = worker_db
    activate(data)
    _, client = data.services.api("parallel-pressure-api")
    with isolated_slice(data, 2) as (lab, start):
        for lane in (1, 2):
            start(lane, tmp_path)
        initial = lab.sample()
        if initial["host_available"] < 3 * 1024**3:
            pytest.skip("Not enough spare WSL memory to inject pressure safely")
        ceiling = lab.profile["memory_bytes"]
        weight = ceiling * 84 // 100 - initial["memory_current"]
        marker = tmp_path / "pressure-ready"
        allocator = ("import mmap, pathlib, sys, time; size=int(sys.argv[1]); pages=mmap.mmap(-1,size); "
                     "[pages.__setitem__(i,1) for i in range(0,size,4096)]; pathlib.Path(sys.argv[2]).touch(); time.sleep(30)")
        pressure = subprocess.Popen(["systemd-run", "--user", "--quiet", "--scope", f"--slice={lab.slice_name}",
                                    sys.executable, "-B", "-c", allocator, str(weight), str(marker)])
        try:
            wait_for(marker.exists, bool, seconds=15)
            sample = lab.sample()
            assert ceiling * 80 <= sample["memory_current"] * 100 < ceiling * 90
            identifier = accept(client, data, "A").json()["comparison_id"]
            for lane in (1, 2):
                wait_for(lambda: data.services.read_log(f"lab-worker-{lane}"),
                         lambda log: "worker_v2_deferred reason=RESOURCE_UNAVAILABLE" in log)
            assert queue_state(client, data, "A", identifier) == "WAITING"
            assert operational_attempt(data, identifier, 1) is None
            assert sample["oom"] == sample["oom_kill"] == 0
        finally:
            data.services.stop(pressure)
        resumed = time.monotonic()
        wait_for(lambda: queue_state(client, data, "A", identifier), lambda state: state == "TERMINAL", seconds=45)
        print(f"parallel_pressure memory_bytes={sample['memory_current']} cap_bytes={ceiling} "
              f"recovery_ms={(time.monotonic()-resumed)*1000:.1f} oom_kills={lab.sample()['oom_kill']}")
