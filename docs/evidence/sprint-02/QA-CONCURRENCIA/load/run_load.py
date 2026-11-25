"""Orquestador de S2-QA-CONCURRENCIA: PostgreSQL temporal + API + worker + Locust.

Uso (Linux/WSL2, desde la raíz del backend, con el venv de pruebas activo):
    NEWGLASS_TEST_PG_BIN=/ruta/a/postgresql/16/bin \
        python -m experiments.load.run_load --out /ruta/salida --scenarios E1,E2,E3 --sha COMMIT

Por cada escenario levanta un clúster PostgreSQL propio en 127.0.0.1, aplica Alembic,
carga datos sintéticos, activa el piloto durable, arranca una API Uvicorn (--workers 1,
como el Procfile) y un worker separado, y ejecuta cada nivel con Locust mientras
muestrea CPU/RSS cada 200 ms. Al terminar detiene todo y borra el clúster.
SECRET_KEY y la contraseña sintética se generan en memoria: no se escriben en disco.
"""
import argparse
from datetime import datetime
from importlib import metadata
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import time

import httpx
import psutil
from sqlalchemy import create_engine, text

from experiments.load.common import require_loopback_database, require_loopback_url
from experiments.load.sampler import ResourceSampler

BACKEND = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
LEVELS = {"E1": [1, 5, 10, 25, 50], "E2": [1, 2, 4, 8, 10], "E3": [2],
          "E1X": [100, 200, 400], "E2b": [10], "E1R": [75, 100], "E1S": [100]}   # complementos exploratorios
REPETITIONS = {"E1": 3, "E2": 3, "E3": 3, "E1X": 3, "E2b": 3, "E1R": 3, "E1S": 3}
FRESH_STACK = {"E1R", "E1S"}  # pila nueva (API, worker y base) en cada repetición
E1S_RATE, E1S_RUN_SECONDS = 2, 115   # E1S: ramp-up lento (50 s) + ventana estable >= 60 s
E2B_STAGGER_MS = 400
E1_RUN_SECONDS = 75          # ramp-up <= 10 s + ventana estable >= 60 s
E2_RUN_CAP_SECONDS = 1200    # tope de seguridad; la corrida termina sola al cerrar todos los trabajos
E3_RUN_CAP_SECONDS = 2400
E3_REPS = int(os.environ.get("QA_E3_REPS", "20"))  # piloto: QA_E3_REPS=2
E1_CLASSES = ["AdministradorReader", "AlmaceneroReader", "OperarioReader"]


def free_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=600, **kwargs)


class Postgres:
    def __init__(self, root: Path, bindir: Path):
        self.root, self.bindir, self.port = root, bindir, free_port()
        self.data = root / "pgdata"

    def start(self):
        result = run([self.bindir / "initdb", "-D", self.data, "-U", "loadqa", "-A", "trust",
                      "--no-locale", "--encoding=UTF8"])
        assert result.returncode == 0, result.stderr
        with (self.data / "postgresql.conf").open("a", encoding="utf-8") as config:
            config.write(f"\nlisten_addresses = '127.0.0.1'\nport = {self.port}\n"
                         "unix_socket_directories = ''\ntimezone = 'UTC'\n")
        result = run([self.bindir / "pg_ctl", "-D", self.data, "-l", self.root / "postgres.log", "-w", "start"])
        assert result.returncode == 0, result.stderr
        admin = create_engine(self.url("postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.exec_driver_sql('CREATE DATABASE "newglass_load"')
        admin.dispose()

    def url(self, database="newglass_load"):
        return f"postgresql+psycopg://loadqa@127.0.0.1:{self.port}/{database}"

    def stop(self):
        if (self.data / "postmaster.pid").exists():
            run([self.bindir / "pg_ctl", "-D", self.data, "-m", "fast", "-w", "stop"])
        shutil.rmtree(self.data, ignore_errors=True)  # solo datos sintéticos


class Stack:
    """Un escenario: clúster, migraciones, datos, piloto, API y worker."""

    def __init__(self, runtime: Path, pg_bin: Path, password: str):
        self.runtime, self.password = runtime, password
        self.pg = Postgres(runtime, pg_bin)
        self.api = self.worker = None
        self.logs = []

    def env(self, **extra):
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("DATABASE_", "DB_", "NEWGLASS_COMPARISON", "LOGIN_", "SECRET"))}
        base.update(PYTHON_DOTENV_DISABLED="1", PYTHONPATH=str(BACKEND), DATABASE_URL=self.pg.url(),
                    SECRET_KEY=self.secret_key, PYTHONIOENCODING="utf-8")
        base.update(extra)
        require_loopback_database(base["DATABASE_URL"])
        return base

    def up(self):
        self.secret_key = secrets.token_urlsafe(48)
        self.pg.start()
        migrated = run([sys.executable, "-B", "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=self.env())
        assert migrated.returncode == 0, migrated.stderr[-4000:]
        self.manifest_path = self.runtime / "accounts.json"
        seeded = run([sys.executable, "-m", "experiments.load.seed_data", "--manifest", self.manifest_path],
                     cwd=BACKEND, env=self.env(LOAD_PASSWORD=self.password))
        assert seeded.returncode == 0, seeded.stderr[-4000:]
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        pilot = run([sys.executable, "-m", "app.modules.optimization.worker_v2", "--enable-pilot"],
                    cwd=BACKEND, env=self.env())
        assert pilot.returncode == 0, pilot.stderr[-4000:]
        port = free_port()
        self.base_url = f"http://127.0.0.1:{port}"
        require_loopback_url(self.base_url)
        # Mismo comando que el Procfile, pero ligado a loopback.
        self.api = self.start("api", ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port),
                                      "--workers", "1"])
        self.worker = self.start("worker", ["-m", "app.modules.optimization.worker_v2"])
        self.wait_ready()

    def start(self, name, args):
        log = (self.runtime / f"{name}.log").open("w", encoding="utf-8")
        self.logs.append(log)
        return subprocess.Popen([sys.executable, *args], cwd=BACKEND, stdout=log, stderr=subprocess.STDOUT,
                                env=self.env(PGAPPNAME=f"load-{name}"))

    def wait_ready(self):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            assert self.api.poll() is None and self.worker.poll() is None, "API o worker terminó al arrancar"
            try:
                api_ok = httpx.get(f"{self.base_url}/health/ready", timeout=2).status_code == 200
            except httpx.TransportError:
                api_ok = False
            if api_ok and "worker_v2_ready" in (self.runtime / "worker.log").read_text(encoding="utf-8"):
                return
            time.sleep(0.25)
        raise RuntimeError("La API o el worker no quedaron listos en 60 s")

    def down(self):
        for process in (self.api, self.worker):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
        for log in self.logs:
            log.close()
        self.pg.stop()

    def sql(self, statement, **params):
        engine = create_engine(self.pg.url())
        try:
            with engine.connect() as connection:
                return [dict(row._mapping) for row in connection.execute(text(statement), params)]
        finally:
            engine.dispose()


@contextmanager
def scenario_stack(runtime, pg_bin, password):
    stack = Stack(runtime, pg_bin, password)
    try:
        stack.up()
        yield stack
    finally:
        stack.down()


def run_locust(stack, scenario, users, rep, out: Path, password):
    run_dir = out / "resultados" / scenario / f"u{users:02d}_r{rep}"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{scenario}u{users}r{rep}{secrets.token_hex(3)}"
    if scenario == "E1S":
        rate, seconds, classes = E1S_RATE, E1S_RUN_SECONDS, E1_CLASSES
    elif scenario in ("E1", "E1X", "E1R"):
        rate, seconds, classes = max(users / 10, 1), E1_RUN_SECONDS, E1_CLASSES
    elif scenario in ("E2", "E2b"):
        rate, seconds, classes = users, E2_RUN_CAP_SECONDS, ["ComparisonUser"]
    else:
        rate, seconds, classes = users, E3_RUN_CAP_SECONDS, ["ConfirmUser"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_", "SECRET"))}
    env.update(PYTHONPATH=str(BACKEND), LOAD_RUN_DIR=str(run_dir), LOAD_RUN_ID=run_id,
               LOAD_ACCOUNTS=str(stack.manifest_path), LOAD_PASSWORD=password, LOAD_TARGET=str(users),
               LOAD_E3_REPS=str(E3_REPS), LOAD_E3_OFFSET=str((rep - 1) * E3_REPS),
               LOAD_STAGGER_MS=str(E2B_STAGGER_MS if scenario == "E2b" else 0))
    command = [sys.executable, "-m", "locust", "-f", HERE / "locustfile.py", "--headless", "-u", users,
               "-r", rate, "-t", f"{seconds}s", "--host", stack.base_url, "--csv", run_dir / "locust",
               "--html", run_dir / "locust_report.html", "--stop-timeout", "30", "--only-summary",
               "--loglevel", "WARNING", *classes]
    targets = {"api": stack.api.pid, "worker": stack.worker.pid}
    started = time.time()
    with ResourceSampler(targets, run_dir / "resources.csv"):
        result = subprocess.run([str(c) for c in command], cwd=BACKEND, env=env, capture_output=True, text=True,
                                timeout=seconds + 300)
    (run_dir / "locust_console.txt").write_text(result.stdout[-20000:] + "\n" + result.stderr[-20000:],
                                                encoding="utf-8")
    meta = dict(scenario=scenario, users=users, repetition=rep, run_id=run_id, spawn_rate=rate,
                run_time_cap_s=seconds, started=started, finished=time.time(), locust_exit=result.returncode,
                classes=classes)
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return run_dir


def probe_recovery(stack, run_dir):
    """Después de la carga: ¿la API vuelve a responder? Sonda de salud a +5, +35 y +65 s."""
    started, probes = time.monotonic(), []
    for target in (5, 35, 65):
        time.sleep(max(0, started + target - time.monotonic()))
        t0 = time.monotonic()
        try:
            status = httpx.get(f"{stack.base_url}/health/ready", timeout=10).status_code
        except httpx.TransportError as error:
            status = type(error).__name__
        probes.append(dict(after_s=target, status=status, ms=round((time.monotonic() - t0) * 1000, 1)))
    (run_dir / "recovery.json").write_text(json.dumps(probes, indent=2), encoding="utf-8")
    return probes


def verify_e2(stack, run_dir):
    orders = stack.manifest["orders"]["comparison"]
    rows = stack.sql("SELECT estado, count(*) AS n FROM pedidos WHERE id_pedido = ANY(:ids) GROUP BY estado",
                     ids=orders)
    jobs = stack.sql("SELECT state, count(*) AS n, count(slot) AS with_slot FROM optimization_worker_jobs GROUP BY state")
    stock = stack.sql("SELECT count(*) AS n FROM movimientos_inventario")
    result = dict(order_states={r["estado"]: r["n"] for r in rows},
                  worker_jobs={r["state"]: dict(n=r["n"], with_slot=r["with_slot"]) for r in jobs},
                  inventory_movements=stock[0]["n"])
    result["ok"] = (result["order_states"] == {"PENDIENTE": len(orders)}
                    and all(v["with_slot"] == 0 for v in result["worker_jobs"].values())
                    and "WAITING" not in result["worker_jobs"] and result["inventory_movements"] == 0)
    (run_dir / "verify.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def verify_e3(stack, run_dir, before):
    sheet = stack.manifest["confirm_sheet_id"]
    records = [json.loads(line) for line in (run_dir / "e3_confirmations.jsonl").read_text(encoding="utf-8").splitlines()]
    after = stack.sql("SELECT cantidad FROM planchas WHERE id_plancha = :id", id=sheet)[0]["cantidad"]
    readback_path = run_dir / "e3_readback.jsonl"
    readback = {r["rep"]: r for r in map(json.loads, readback_path.read_text(encoding="utf-8").splitlines())}         if readback_path.exists() else {}
    reps = []
    for rep in sorted({r["rep"] for r in records}):
        pair = [r for r in records if r["rep"] == rep]
        accepted = [r for r in pair if r["status"] == 200]
        cid, execution = pair[0]["comparison_id"], pair[0]["execution_id"]
        moves = stack.sql("SELECT id_plancha, id_retazo, coalesce(cantidad_afectada, 1) AS qty, tipo_evento "
                          "FROM movimientos_inventario WHERE id_optimizacion = :cid", cid=cid)
        remnants = stack.sql("SELECT count(*) AS n FROM retazos WHERE id_ejecucion_origen = :e", e=execution)[0]["n"]
        order = stack.sql("SELECT estado FROM pedidos WHERE id_pedido = :id", id=pair[0]["order_id"])[0]["estado"]
        # Recibo persistido en la traza (existe aunque la respuesta HTTP haya sido un error).
        trace = stack.sql("SELECT comparison_snapshot->'confirmation' AS confirmation, "
                          "comparison_snapshot->'runtime'->>'supervisor_stopped_at' AS stopped_at "
                          "FROM optimizaciones WHERE id_optimizacion = :cid", cid=cid)[0]
        confirmation = trace["confirmation"] or {}
        stored = (confirmation.get("receipt") or {})
        stored_materials = stored.get("materials_consumed") or []
        stored_sheets = sum(1 for m in stored_materials
                            if str(m.get("source")).upper() == "PLANCHA" and m.get("material_id") == sheet)
        stored_remnant_ids = sorted(m["material_id"] for m in stored_materials if str(m.get("source")).upper() == "RETAZO")
        stored_created = len(stored.get("remnants_created") or [])
        moved_sheets = sum(m["qty"] for m in moves if m["id_plancha"] == sheet)
        moved_remnant_ids = sorted(m["id_retazo"] for m in moves if m["id_retazo"] is not None)
        # Integridad (RIn-1): el efecto en inventario es exactamente el de UNA confirmación.
        single_effect = (bool(confirmation) and moved_sheets == stored_sheets and moved_remnant_ids == stored_remnant_ids
                         and len(set(moved_remnant_ids)) == len(moved_remnant_ids) and remnants == stored_created
                         and order == "CONFIRMADO")
        if accepted:
            receipt = accepted[0]["materials_consumed"] or []
            single_effect = single_effect and sum(
                1 for m in receipt if m.get("material_source") == "PLANCHA" and m.get("material_id") == sheet) == stored_sheets
        # Respuesta (fiabilidad): una 200 y la otra 409 sin efecto.
        response_ok = len(accepted) == 1 and all(
            r["status"] == 409 and r["code"] in ("ALREADY_CONFIRMED", "STATE_CONFLICT") for r in pair if r["status"] != 200)
        confirmed_at = confirmation.get("confirmed_at")
        reps.append(dict(rep=rep, comparison_id=cid, statuses=sorted(r["status"] for r in pair),
                         codes=sorted(r["code"] for r in pair),
                         send_skew_ms=round(abs(pair[0]["sent"] - pair[-1]["sent"]) * 1000, 2),
                         accepted=len(accepted), stored_sheet_units=stored_sheets, movement_sheet_units=moved_sheets,
                         stored_remnants_consumed=stored_remnant_ids, movement_remnants_consumed=moved_remnant_ids,
                         stored_remnants_created=stored_created, remnants_in_db=remnants, order_state=order,
                         confirmed_at=confirmed_at, supervisor_stopped_at=trace["stopped_at"],
                         confirmed_before_stop=(confirmed_at is not None and trace["stopped_at"] is not None
                                                and datetime.fromisoformat(confirmed_at)
                                                < datetime.fromisoformat(trace["stopped_at"])),
                         readback_status=readback.get(rep, {}).get("status"),
                         single_effect=single_effect, double_discount=not single_effect, response_ok=response_ok))
    consumed = sum(r["stored_sheet_units"] for r in reps)
    result = dict(sheet_before=before, sheet_after=after, sheet_delta=before - after, stored_sheet_units=consumed,
                  repetitions=len(reps), double_discounts=sum(r["double_discount"] for r in reps),
                  incorrect_responses=sum(not r["response_ok"] for r in reps),
                  unreadable_after_confirm=sum(r["readback_status"] not in (200, None) for r in reps), reps=reps)
    result["ok"] = result["double_discounts"] == 0 and result["sheet_delta"] == consumed and len(reps) == E3_REPS
    (run_dir / "verify.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def environment(args, pg_bin):
    def version(command):
        try:
            return run(command).stdout.strip().splitlines()[0]
        except (OSError, IndexError):
            return "desconocido"
    cpu = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")), platform.processor())
    os_release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    return dict(backend_sha=args.sha, os=os_release.get("PRETTY_NAME", "").strip('"'), kernel=platform.release(),
                wsl="microsoft" in platform.release().lower(), cpu_model=cpu, logical_cpus=psutil.cpu_count(),
                ram_gib=round(psutil.virtual_memory().total / 2**30, 2), python=platform.python_version(),
                postgresql=version([pg_bin / "postgres", "--version"]), locust=metadata.version("locust"),
                uvicorn=version([sys.executable, "-m", "uvicorn", "--version"]),
                api_command="uvicorn main:app --host 127.0.0.1 --workers 1 (Procfile en loopback)",
                worker_command="python -m app.modules.optimization.worker_v2 (piloto durable, 1 plaza)",
                sample_interval_ms=200, e1_run_seconds=E1_RUN_SECONDS, e3_reps=E3_REPS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--scenarios", default="E1,E2,E3")
    parser.add_argument("--sha", default="desconocido")
    parser.add_argument("--levels", help="Subconjunto de niveles (piloto), por ejemplo 1,2")
    parser.add_argument("--reps", type=int, help="Repeticiones por nivel (piloto)")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        raise SystemExit("El worker separado y el muestreo requieren Linux (WSL2).")
    pg_bin = Path(os.environ["NEWGLASS_TEST_PG_BIN"])
    out = Path(args.out)
    (out / "resultados").mkdir(parents=True, exist_ok=True)
    (out / "resultados" / "environment.json").write_text(json.dumps(environment(args, pg_bin), indent=2),
                                                         encoding="utf-8")
    password = secrets.token_urlsafe(18)  # solo en memoria y en el entorno de los hijos
    for scenario in args.scenarios.split(","):
        runtime = out / "runtime" / scenario
        shutil.rmtree(runtime, ignore_errors=True)
        runtime.mkdir(parents=True)
        levels = [int(x) for x in args.levels.split(",")] if args.levels and scenario != "E3" else LEVELS[scenario]
        if scenario in FRESH_STACK:
            for users in levels:
                for rep in range(1, (args.reps or REPETITIONS[scenario]) + 1):
                    shutil.rmtree(runtime, ignore_errors=True)
                    runtime.mkdir(parents=True)
                    with scenario_stack(runtime, pg_bin, password) as stack:
                        print(f"[{time.strftime('%H:%M:%S')}] {scenario} usuarios={users} repetición={rep} (pila nueva)", flush=True)
                        run_dir = run_locust(stack, scenario, users, rep, out, password)
                        print("  recuperación:", probe_recovery(stack, run_dir), flush=True)
                        log = (runtime / "api.log").read_text(encoding="utf-8", errors="replace").splitlines()
                        errors = {}
                        for line in log:
                            if line.startswith(("sqlalchemy.", "psycopg.")) or line.startswith("ERROR:"):
                                key = line.split(" (Background")[0][:160]
                                errors[key] = errors.get(key, 0) + 1
                        summary = dict(access_200=sum('" 200 OK' in l for l in log),
                                       access_500=sum('" 500 Internal' in l for l in log), errors=errors)
                        (run_dir / "api_errors.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(f"[{time.strftime('%H:%M:%S')}] {scenario} terminado", flush=True)
            continue
        with scenario_stack(runtime, pg_bin, password) as stack:
            counts = dict(stack.manifest["counts"])
            (out / "resultados" / f"{scenario}_seed_counts.json").write_text(json.dumps(counts, indent=2),
                                                                             encoding="utf-8")
            for users in levels:
                for rep in range(1, (args.reps or REPETITIONS[scenario]) + 1):
                    before = None
                    if scenario == "E3":
                        before = stack.sql("SELECT cantidad FROM planchas WHERE id_plancha = :id",
                                           id=stack.manifest["confirm_sheet_id"])[0]["cantidad"]
                    print(f"[{time.strftime('%H:%M:%S')}] {scenario} usuarios={users} repetición={rep}", flush=True)
                    run_dir = run_locust(stack, scenario, users, rep, out, password)
                    if scenario in ("E2", "E2b"):
                        print("  verificación:", verify_e2(stack, run_dir)["ok"], flush=True)
                    elif scenario == "E3":
                        print("  verificación:", verify_e3(stack, run_dir, before)["ok"], flush=True)
                    time.sleep(5 if scenario in ("E1", "E1X") else 2)
        print(f"[{time.strftime('%H:%M:%S')}] {scenario} terminado", flush=True)


if __name__ == "__main__":
    main()
