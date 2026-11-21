"""Real HTTP, separate Linux processes and transactional PostgreSQL inventory."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from time import perf_counter
import os

import httpx
import pytest
from sqlalchemy import create_engine, delete, func, select, text

from app.models import OptimizationProgressEvent, OptimizationProgressStream, OptimizationStockProtection, Plancha, RolPermiso
from tests.integration.postgres_support import downgrade, upgrade
from .test_comparison_v2_http_restart import (BASE, FF, accept, accept_ff, activate, cancellation,
    ff_state, queue_state, summary, wait_for, worker_db)  # noqa: F401

INVENTORY = "/api/inventory/planchas"
METHODS = ["FF", "BF", "WF", "HF", "M5", "M6-BS"]


def progress(client, data, actor, identifier, *, base=BASE, **params):
    return client.get(f"{base}/{identifier}/progress", headers=data.headers[actor], params=params)


def progress_page(*args, **kwargs):
    response = progress(*args, **kwargs)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def process_sample(pid):
    stats = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    cpu_ms = (int(stats[11]) + int(stats[12])) * 1000 // os.sysconf("SC_CLK_TCK")
    rss = int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    return cpu_ms, rss


def test_three_sessions_work_during_v2_and_resume_progress_across_apis(worker_db):
    data = worker_db
    activate(data, mixed=True)
    with data.sessions.begin() as db:
        db.execute(text("SELECT setval(pg_get_serial_sequence('planchas', 'id_plancha'), 1)"))
    a_server, api_a = data.services.api("progress-api-a")
    b_server, api_b = data.services.api("progress-api-b")
    operator = data.services.stack.enter_context(httpx.Client(base_url=api_b.base_url, timeout=8))
    warehouse = data.services.stack.enter_context(httpx.Client(base_url=api_b.base_url, timeout=8))
    a = accept(api_a, data, "A").json()["comparison_id"]
    assert progress_page(api_b, data, "A", a, limit=1)["events"][0]["kind"] == "WAITING"
    b = accept(operator, data, "O").json()["comparison_id"]
    cancelled = accept(operator, data, "O", order=data.orders["L"], key="qa-progress-to-cancel").json()["comparison_id"]
    worker = data.services.worker("progress-worker", hold=data.orders["A"])
    active = wait_for(lambda: progress_page(api_b, data, "A", a), lambda page: page["current_engine"] is not None)
    assert active["current_engine"]["method"] == "FF"
    assert [event["kind"] for event in active["events"]] == ["WAITING", "RUNNING", "ENGINE_STARTED"]
    assert progress_page(operator, data, "O", b)["state"] == "WAITING"
    assert progress(operator, data, "O", a).status_code == 404
    assert progress(api_a, data, "A", b).status_code == 404
    assert cancellation(operator, data, "O", summary(api_a, data, "A", a)).status_code == 404
    cursor = active["next_sequence"]
    assert progress_page(api_a, data, "A", a, after_sequence=cursor)["events"] == []

    def poll_with_latency(_):
        started = perf_counter()
        page = progress_page(api_b, data, "A", a, after_sequence=cursor)
        assert page["state"] == "RUNNING"
        return (perf_counter() - started) * 1000

    admin_before, worker_before = process_sample(b_server.pid)[0], process_sample(worker.pid)[0]
    headers = data.headers["L"]
    with ThreadPoolExecutor(4) as requests:
        samples = requests.map(poll_with_latency, range(6))
        created = warehouse.post(INVENTORY, headers=headers, json=dict(ancho_mm=8, alto_mm=8, espesor_mm=6,
            cantidad=2, id_tipo_vidrio=7))
        assert created.status_code == 201, created.text
        milliseconds = list(samples)
    independent = created.json()["id_plancha"]
    assert warehouse.patch(f"{INVENTORY}/{independent}", headers=headers, json={"cantidad": 3}).json()["cantidad"] == 3
    assert warehouse.get(INVENTORY, headers=headers).status_code == 200
    assert warehouse.patch(f"{INVENTORY}/1", headers=headers, json={"cantidad": 0}).status_code == 409
    assert warehouse.patch(f"{INVENTORY}/1", headers=headers, json={"ancho_mm": 7}).status_code == 409
    manual = dict(tipo_evento="BAJA_MERMA", motivo="Prueba aislada de stock", cantidad_afectada=1)
    assert warehouse.post(f"{INVENTORY}/1/baja", headers=headers, json=manual).status_code == 409
    assert warehouse.get(f"{INVENTORY}/1/movimientos", headers=headers).json() == []
    written_off = warehouse.post(f"{INVENTORY}/{independent}/baja", headers=headers, json=manual)
    assert written_off.status_code == 201 and written_off.json()["cantidad"] == 2, written_off.text
    assert warehouse.get(f"{INVENTORY}/{independent}/movimientos", headers=headers).json() == [written_off.json()["movimiento"]]
    assert accept(warehouse, data, "L").status_code == 403
    assert warehouse.post(f"{BASE}/{a}/confirm", headers={**headers, "Idempotency-Key": "qa-no-confirm"},
        json={"execution_id": active["current_engine"]["execution_id"], "expected_version": active["version"]}).status_code == 403

    before_cancel = summary(operator, data, "O", cancelled)
    with pytest.raises(RuntimeError, match="cancel rollback"):
        with data.uow() as tx:
            tx.comparisons_v2.request_cancellation(cancelled, data.actors["O"], "qa-progress-rolled-back",
                                                   before_cancel["version"])
            raise RuntimeError("cancel rollback")
    assert progress_page(operator, data, "O", cancelled)["cancellation"] == "NONE"
    response = cancellation(operator, data, "O", before_cancel)
    assert response.status_code == 200 and response.json()["cancel_state"] == "CANCELLED", response.text
    stopped = progress_page(operator, data, "O", cancelled)
    assert stopped["state"] == "TERMINAL" and stopped["cancellation"] == "CANCELLED"
    assert [row["kind"] for row in stopped["events"]] == ["WAITING", "CANCELLATION_REQUESTED", "TERMINAL"]
    with data.sessions() as db:
        assert set(db.scalars(select(OptimizationStockProtection.job_id))) == {a, b}
        connections = dict(db.execute(text("SELECT application_name, count(*) FROM pg_stat_activity "
            "WHERE datname=current_database() AND application_name LIKE 'progress-%' GROUP BY application_name")).all())
        assert db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
            "AND application_name LIKE 'progress-%' AND state='idle in transaction' "
            "AND clock_timestamp()-xact_start > interval '1 second'")) == 0
    admin_cpu, admin_rss = process_sample(b_server.pid)
    worker_cpu, worker_rss = process_sample(worker.pid)
    print(f"progress_http samples={len(milliseconds)} median_ms={median(milliseconds):.2f} "
          f"max_ms={max(milliseconds):.2f} page_bytes={len(progress(api_b, data, 'A', a).content)} "
          f"api_cpu_ms={admin_cpu-admin_before} api_rss={admin_rss} worker_cpu_ms={worker_cpu-worker_before} "
          f"worker_rss={worker_rss} connections={connections}")

    data.services.stop(a_server)
    api_a.close()
    _, restarted = data.services.api("progress-api-restarted")
    assert progress_page(restarted, data, "A", a, after_sequence=cursor)["state"] == "RUNNING"
    assert progress_page(restarted, data, "O", b)["state"] == "WAITING"
    assert accept(restarted, data, "A").json()["comparison_id"] == a
    data.services.resume_file.touch()
    wait_for(lambda: queue_state(restarted, data, "O", b), lambda state: state == "TERMINAL")
    a_final = progress_page(restarted, data, "A", a, after_sequence=cursor)
    b_final = progress_page(operator, data, "O", b)
    assert a_final["state"] == b_final["state"] == "TERMINAL"
    assert a_final["current_engine"] is None and b_final["current_engine"] is None
    assert [row["method"] for row in a_final["events"] if row["kind"] == "ENGINE_FINISHED"] == METHODS
    assert [row["method"] for row in b_final["events"] if row["kind"] == "ENGINE_STARTED"] == METHODS
    assert [row["sequence"] for row in a_final["events"]] == list(range(cursor + 1, a_final["next_sequence"] + 1))
    page = progress_page(restarted, data, "A", a, after_sequence=cursor, limit=2)
    assert page["has_more"] and page["events"] == a_final["events"][:2]
    assert all(item["complete"] for item in summary(restarted, data, "A", a)["executions"])
    assert all(item["complete"] for item in summary(operator, data, "O", b)["executions"])
    with data.sessions() as db:
        assert db.get(Plancha, 1).cantidad == 1 and db.get(Plancha, independent).cantidad == 2
        assert db.scalar(select(func.count()).select_from(OptimizationStockProtection)) == 0
    assert warehouse.get(f"{INVENTORY}/1/movimientos", headers=headers).json() == []


def test_ff_replays_retained_events_and_recovers_expired_cursor_from_snapshot(worker_db):
    data = worker_db
    activate(data, mixed=True)
    data.services.environment["NEWGLASS_PROGRESS_RETAINED_EVENTS"] = "3"
    _, client = data.services.api("retained-ff-api")
    identifier = accept_ff(client, data, "A").json()["id_optimizacion"]
    first = progress_page(client, data, "A", identifier, base=FF)
    assert first["state"] == "WAITING" and first["next_sequence"] == 1
    data.services.worker("retained-ff-worker")
    wait_for(lambda: ff_state(client, data, "A", identifier), lambda state: state == "TERMINAL")
    missing = progress(client, data, "A", identifier, base=FF, after_sequence=1)
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "EVENT_CURSOR_EXPIRED"
    last = missing.json()["detail"]["resume_after_sequence"]
    terminal = progress_page(client, data, "A", identifier, base=FF)
    assert [row["kind"] for row in terminal["events"]] == ["ENGINE_STARTED", "ENGINE_FINISHED", "TERMINAL"]
    assert terminal["next_sequence"] == last == 5
    assert progress(client, data, "O", identifier, base=FF).status_code == 404
    with data.sessions.begin() as db:
        db.execute(text("UPDATE optimization_progress_events SET occurred_at=clock_timestamp()-interval '2 hours' "
                        "WHERE job_id=:id"), {"id": identifier})
    assert progress(client, data, "A", identifier, base=FF, after_sequence=4).status_code == 409
    with data.uow() as tx:
        tx.worker_jobs.progress.purge_expired()
    with data.sessions() as db:
        assert db.scalar(select(func.count()).select_from(OptimizationProgressEvent)) == 0
    recovered = progress_page(client, data, "A", identifier, base=FF, after_sequence=last)
    assert recovered["events"] == [] and recovered["result_state"] == "COMPLETADA"
    assert client.get(recovered["result_url"], headers=data.headers["A"]).json()["completo"]


def test_saturation_revocation_and_postgres_reconnection_are_safe(worker_db):
    data = worker_db
    activate(data)
    data.services.environment.update(NEWGLASS_PROGRESS_MAX_READERS="1", NEWGLASS_PROGRESS_READS_PER_SECOND="2")
    _, client = data.services.api("bounded-progress-api")
    identifier = accept(client, data, "O").json()["comparison_id"]
    with ThreadPoolExecutor(6) as requests:
        responses = list(requests.map(lambda _: progress(client, data, "O", identifier), range(6)))
    assert {response.status_code for response in responses} == {200, 429}
    assert all(resp.headers["Retry-After"] == "1" for resp in responses if resp.status_code == 429)
    admin = create_engine(data.cluster, isolation_level="AUTOCOMMIT")
    database = data.engine.url.database
    try:
        with admin.connect() as control:
            assert database.startswith("r1_") and database[3:].isalnum()
            control.exec_driver_sql(f'ALTER DATABASE "{database}" WITH ALLOW_CONNECTIONS false')
            try:
                control.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                     "WHERE datname=:db AND application_name='bounded-progress-api'"), {"db": database})
                unavailable = wait_for(lambda: progress(client, data, "O", identifier),
                                       lambda response: response.status_code != 429)
                assert unavailable.status_code == 503 and unavailable.json()["detail"]["code"] == "RESOURCE_UNAVAILABLE"
            finally:
                control.exec_driver_sql(f'ALTER DATABASE "{database}" WITH ALLOW_CONNECTIONS true')
    finally:
        admin.dispose()
    recovered = wait_for(lambda: progress(client, data, "O", identifier, after_sequence=1),
                         lambda response: response.status_code == 200).json()
    assert recovered["state"] == "WAITING" and recovered["events"] == []
    with data.sessions.begin() as db:
        db.execute(delete(RolPermiso).where(RolPermiso.id_rol == data.role_ids["O"],
                                           RolPermiso.id_permiso == data.codes["optimization.view"]))
    forbidden = wait_for(lambda: progress(client, data, "O", identifier), lambda response: response.status_code != 429)
    assert forbidden.status_code == 403
    assert client.get(INVENTORY, headers=data.headers["L"]).status_code == 200


def test_open_stock_protection_blocks_confirmation_until_only_that_job_closes(worker_db):
    data = worker_db
    activate(data, mixed=True)
    _, client = data.services.api("protected-confirm-api")
    a = accept(client, data, "A").json()["comparison_id"]
    worker = data.services.worker("protected-confirm-worker")
    wait_for(lambda: queue_state(client, data, "A", a), lambda state: state == "TERMINAL")
    data.services.stop(worker)
    completed = summary(client, data, "A", a)
    execution = completed["executions"][0]["execution_id"]
    selection = client.put(f"{BASE}/{a}/selection", headers=data.headers["A"],
        json={"execution_id": execution, "expected_version": completed["version"]})
    assert selection.status_code == 200, selection.text
    b = accept_ff(client, data, "O").json()["id_optimizacion"]
    c = accept(client, data, "O", order=data.orders["L"], key="qa-protected-confirm-v2").json()["comparison_id"]

    def confirm():
        return client.post(f"{BASE}/{a}/confirm", headers={**data.headers["A"], "Idempotency-Key": "qa-protected-confirm"},
            json={"execution_id": execution, "expected_version": selection.json()["version"]})

    rejected = confirm()
    assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "STOCK_CONFLICT"
    assert client.get(f"{INVENTORY}/1/movimientos", headers=data.headers["L"]).json() == []
    assert cancellation(client, data, "O", summary(client, data, "O", c)).json()["cancel_state"] == "CANCELLED"
    assert confirm().status_code == 409  # Cancelling V2 does not release FF's reference to the same unit.
    assert progress_page(client, data, "O", b, base=FF)["state"] == "WAITING"
    data.services.worker("protected-confirm-resume")
    wait_for(lambda: ff_state(client, data, "O", b), lambda state: state == "TERMINAL")
    first = confirm()
    assert first.status_code == 200 and confirm().json() == first.json()
    movements = client.get(f"{INVENTORY}/1/movimientos", headers=data.headers["L"]).json()
    assert [(row["tipo_evento"], row["cantidad_afectada"], row["id_optimizacion"]) for row in movements] == [
        ("CONSUMO_OPTIMIZACION", 1, a)]
    with data.sessions() as db:
        assert db.get(Plancha, 1).cantidad == 0


def test_additive_migration_backfills_old_open_references_without_history_events(worker_db):
    data = worker_db
    activate(data, mixed=True)
    _, client = data.services.api("old-progress-api")
    a = accept(client, data, "A").json()["comparison_id"]
    b = accept_ff(client, data, "O").json()["id_optimizacion"]
    # Form the exact old database boundary: the jobs and snapshots existed, the new projections did not.
    with data.sessions.begin() as db:
        db.execute(delete(OptimizationProgressStream))
        db.execute(delete(OptimizationStockProtection))
    prior = downgrade(data.engine, "s2conc01b210")
    assert prior.returncode == 0, prior.stderr
    updated = upgrade(data.engine, "head")
    assert updated.returncode == 0, updated.stderr
    assert progress_page(client, data, "A", a)["events"] == []
    assert progress_page(client, data, "O", b, base=FF)["next_sequence"] == 0
    with data.sessions() as db:
        expected = {(a, "PLANCHA", 1, 1), (b, "PLANCHA", 1, 1)}
        assert set(db.execute(select(OptimizationStockProtection.job_id, OptimizationStockProtection.source,
            OptimizationStockProtection.material_id, OptimizationStockProtection.units))) == expected
    assert client.patch(f"{INVENTORY}/1", headers=data.headers["L"], json={"cantidad": 0}).status_code == 409
