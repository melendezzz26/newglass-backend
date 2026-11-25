"""Escenarios Locust de S2-QA-CONCURRENCIA (E1 lecturas, E2 cola V2, E3 confirmación).

Lo lanza run_load.py; no está pensado para apuntarlo a mano contra otro servidor.
Variables de entorno:
    LOAD_RUN_DIR    carpeta de salida de la corrida (registro por petición y JSONL)
    LOAD_ACCOUNTS   manifiesto de cuentas y pedidos sintéticos (fuera de la evidencia)
    LOAD_PASSWORD   contraseña sintética efímera (nunca se escribe en disco)
    LOAD_RUN_ID     identificador de la corrida, usado en las claves de idempotencia
    LOAD_TARGET     usuarios esperados (E2/E3): barrera de inicio simultáneo
    LOAD_E3_REPS    repeticiones de E3

RN-01: aborta si el host no es loopback. RN-02: cada usuario inicia sesión una sola
vez, en on_start, con credenciales válidas (no provoca bloqueos de EN-SEC-001).
"""
import csv
import itertools
import json
import os
import random
import time
from collections import defaultdict

import gevent
from gevent.event import Event
from locust import HttpUser, between, constant, events, task
from locust.exception import StopUser

from experiments.load.common import require_loopback_url

RUN_DIR = os.environ["LOAD_RUN_DIR"]
RUN_ID = os.environ["LOAD_RUN_ID"]
TARGET = int(os.environ.get("LOAD_TARGET", "0"))
E3_REPS = int(os.environ.get("LOAD_E3_REPS", "20"))
E3_OFFSET = int(os.environ.get("LOAD_E3_OFFSET", "0"))  # primer pedido de esta corrida de E3
STAGGER_S = int(os.environ.get("LOAD_STAGGER_MS", "0")) / 1000  # E2b: envíos escalonados
with open(os.environ["LOAD_ACCOUNTS"], encoding="utf-8") as _handle:
    DATA = json.load(_handle)
PASSWORD = os.environ["LOAD_PASSWORD"]
BASE = "/api/optimization/comparisons-v2"
POLL_DEADLINE_S = 900
CONTROLLED = {(503, "OPTIMIZER_BUSY"), (429, "PROGRESS_BUSY")}

_counters = defaultdict(itertools.count)
_phases = {}
_log = {"handle": None, "writer": None}


def _write_phases():
    with open(os.path.join(RUN_DIR, "phases.json"), "w", encoding="utf-8") as handle:
        json.dump(_phases, handle, indent=2)


def _append_jsonl(name, record):
    with open(os.path.join(RUN_DIR, name), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def error_code(response):
    if response is None or response.status_code < 400:
        return ""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return ""
    return detail.get("code", "") if isinstance(detail, dict) else ""


@events.test_start.add_listener
def _on_test_start(environment, **_kwargs):
    require_loopback_url(environment.host)
    handle = open(os.path.join(RUN_DIR, "requests.csv"), "w", newline="", encoding="utf-8")
    writer = csv.writer(handle)
    writer.writerow(["t_start_epoch_s", "method", "name", "status", "response_ms", "code", "retry_after"])
    _log.update(handle=handle, writer=writer)
    _phases["test_start"] = time.time()
    _write_phases()


@events.spawning_complete.add_listener
def _on_spawning_complete(user_count, **_kwargs):
    _phases.setdefault("spawning_complete", time.time())
    _phases["user_count"] = user_count
    _write_phases()


@events.test_stop.add_listener
def _on_test_stop(environment, **_kwargs):
    _phases["test_stop"] = time.time()
    _write_phases()
    if _log["handle"] is not None:
        _log["handle"].close()
        _log.update(handle=None, writer=None)


@events.request.add_listener
def _on_request(request_type, name, response_time, response_length, response=None,
                exception=None, start_time=None, **_kwargs):
    writer = _log["writer"]
    if writer is None:
        return
    status = response.status_code if response is not None and hasattr(response, "status_code") else -1
    started = start_time if start_time is not None else time.time() - response_time / 1000
    retry = response.headers.get("Retry-After", "") if status >= 400 else ""
    writer.writerow([f"{started:.4f}", request_type, name, status, f"{response_time:.2f}",
                     error_code(response) if status >= 400 else "", retry])


def metric(name, value_ms, request_type="METRIC"):
    """Publica una medida derivada (espera, total) para que aparezca en el CSV/HTML de Locust."""
    events.request.fire(request_type=request_type, name=name, response_time=value_ms,
                        response_length=0, exception=None, context={})


class GBarrier:
    """Barrera de greenlets: todos los participantes salen juntos."""

    def __init__(self, parties):
        self.parties, self.count, self.event = parties, 0, Event()

    def wait(self, timeout=300):
        self.count += 1
        if self.count >= self.parties:
            self.event.set()
        elif not self.event.wait(timeout):
            raise StopUser()


class LoggedUser(HttpUser):
    abstract = True
    pool = None

    def on_start(self):
        accounts = DATA["accounts"][self.pool]
        self.index = next(_counters[self.pool])
        account = accounts[self.index % len(accounts)]
        self.user_id = account["user_id"]
        with self.client.post("/api/auth/login", json={"username": account["username"], "password": PASSWORD},
                              name="POST /api/auth/login", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"login {response.status_code}")
                raise StopUser()
            token = response.json()["access_token"]
        self.client.headers["Authorization"] = f"Bearer {token}"


# ---------------------------------------------------------------- E1: lecturas
class ReadUser(LoggedUser):
    abstract = True
    wait_time = between(0.2, 1.0)

    def orders(self):
        self.client.get(f"/api/orders?page={random.randint(1, 15)}&limit=20", name="GET /api/orders")

    def order_detail(self):
        order = random.choice(DATA["orders"]["read"])
        self.client.get(f"/api/orders/{order}", name="GET /api/orders/[id]")

    def sheets(self):
        self.client.get("/api/inventory/planchas", name="GET /api/inventory/planchas")

    def remnants(self):
        self.client.get("/api/inventory/retazos", name="GET /api/inventory/retazos")

    def glass_types(self):
        self.client.get("/api/inventory/tipos-vidrio", name="GET /api/inventory/tipos-vidrio")

    def clients(self):
        self.client.get("/api/clients", name="GET /api/clients")

    def history(self):
        self.client.get(f"{BASE}/history", name="GET /comparisons-v2/history")

    def me(self):
        self.client.get("/api/auth/me", name="GET /api/auth/me")


class AdministradorReader(ReadUser):
    weight = 3
    pool = "admin"

    @task(3)
    def t_orders(self): self.orders()

    @task(2)
    def t_order_detail(self): self.order_detail()

    @task(3)
    def t_sheets(self): self.sheets()

    @task(2)
    def t_remnants(self): self.remnants()

    @task(1)
    def t_glass(self): self.glass_types()

    @task(1)
    def t_clients(self): self.clients()

    @task(1)
    def t_users(self): self.client.get("/api/users", name="GET /api/users")

    @task(1)
    def t_roles(self): self.client.get("/api/roles", name="GET /api/roles")

    @task(1)
    def t_history(self): self.history()

    @task(1)
    def t_me(self): self.me()


class AlmaceneroReader(ReadUser):
    weight = 3
    pool = "almacenero"

    @task(4)
    def t_sheets(self): self.sheets()

    @task(3)
    def t_remnants(self): self.remnants()

    @task(2)
    def t_glass(self): self.glass_types()

    @task(1)
    def t_history(self): self.history()

    @task(1)
    def t_me(self): self.me()


class OperarioReader(ReadUser):
    weight = 4
    pool = "operario"

    @task(4)
    def t_orders(self): self.orders()

    @task(3)
    def t_order_detail(self): self.order_detail()

    @task(2)
    def t_sheets(self): self.sheets()

    @task(1)
    def t_clients(self): self.clients()

    @task(1)
    def t_glass(self): self.glass_types()

    @task(1)
    def t_history(self): self.history()

    @task(1)
    def t_me(self): self.me()


# ------------------------------------------------- E2/E3: utilidades comunes
_start_barrier = {"barrier": None}
_finished = {"count": 0}


def start_barrier():
    if _start_barrier["barrier"] is None:
        _start_barrier["barrier"] = GBarrier(TARGET)
    return _start_barrier["barrier"]


class QueueUser(LoggedUser):
    abstract = True
    wait_time = constant(0)

    def finish(self):
        _finished["count"] += 1
        if _finished["count"] >= TARGET:
            gevent.spawn_later(1, self.environment.runner.quit)
        raise StopUser()

    def admit(self, order_id, key):
        sent, sent_mono = time.time(), time.monotonic()
        with self.client.post(BASE, json={"order_ids": [order_id]}, headers={"Idempotency-Key": key},
                              name="POST /comparisons-v2 (admisión)", catch_response=True) as response:
            received_mono = time.monotonic()
            code = error_code(response)
            if response.status_code == 201:
                body = response.json()
            elif (response.status_code, code) in CONTROLLED:
                response.success()  # RN-04: rechazo controlado, no es un fallo
                body = None
            else:
                response.failure(f"{response.status_code} {code}")
                body = None
        return dict(sent=sent, sent_mono=sent_mono, admission_ms=(received_mono - sent_mono) * 1000,
                    status=response.status_code, code=code,
                    retry_after=response.headers.get("Retry-After"), body=body)

    def follow(self, comparison_id):
        """Sondea /progress respetando poll_after_ms y Retry-After (RN-05)."""
        seen, after, page = [], None, None
        progress = {"200": 0, "429": 0, "other": 0}
        deadline = time.time() + POLL_DEADLINE_S
        while time.time() < deadline:
            params = {"limit": 32} if after is None else {"limit": 32, "after_sequence": after}
            with self.client.get(f"{BASE}/{comparison_id}/progress", params=params,
                                 name="GET /comparisons-v2/[id]/progress", catch_response=True) as response:
                code = error_code(response)
                if response.status_code == 200:
                    page = response.json()
                    progress["200"] += 1
                elif (response.status_code, code) in CONTROLLED:
                    response.success()
                    progress["429"] += 1
                    gevent.sleep(float(response.headers.get("Retry-After", "1")))
                    continue
                elif response.status_code == 409 and code == "EVENT_CURSOR_EXPIRED":
                    response.success()
                    after = None
                    continue
                else:
                    response.failure(f"{response.status_code} {code}")
                    progress["other"] += 1
                    gevent.sleep(1)
                    continue
            seen.extend(page["events"])
            after = page["next_sequence"]
            if page["state"] == "TERMINAL" and not page["has_more"]:
                return dict(terminal_observed=time.monotonic(), events=seen, page=page, polls=progress)
            if not page["has_more"]:
                gevent.sleep(page["poll_after_ms"] / 1000)
        return dict(terminal_observed=None, events=seen, page=page, polls=progress)

    def summary(self, comparison_id):
        response = self.client.get(f"{BASE}/{comparison_id}", name="GET /comparisons-v2/[id]")
        return response.json() if response.status_code == 200 else None


def event_times(events_seen):
    first = {}
    for event in events_seen:
        first.setdefault(event["kind"], event["occurred_at"])
    return first


# --------------------------------------------------- E2: comparaciones en cola
class ComparisonUser(QueueUser):
    pool = "comparison"

    @task
    def compare(self):
        start_barrier().wait(timeout=180)
        if STAGGER_S:
            gevent.sleep(self.index * STAGGER_S)  # E2b: evita choques de admisión para observar la cola llena
        order_id = DATA["orders"]["comparison"][self.index]
        admission = self.admit(order_id, f"load-{RUN_ID}-u{self.index}")
        record = dict(run_id=RUN_ID, user_index=self.index, order_id=order_id, sent=admission["sent"],
                      admission_ms=admission["admission_ms"],
                      status=admission["status"], code=admission["code"], retry_after=admission["retry_after"])
        if admission["status"] != 201:
            _append_jsonl("e2_comparisons.jsonl", record)
            self.finish()
        comparison_id = admission["body"]["comparison_id"]
        followed = self.follow(comparison_id)
        summary = self.summary(comparison_id)
        released = None
        if summary is not None:
            with self.client.post(f"{BASE}/{comparison_id}/release", json={"expected_version": summary["version"]},
                                  name="POST /comparisons-v2/[id]/release", catch_response=True) as response:
                released = response.status_code
        times = event_times(followed["events"])
        to_terminal = (None if followed["terminal_observed"] is None
                       else (followed["terminal_observed"] - admission["sent_mono"]) * 1000)
        record.update(comparison_id=comparison_id, client_to_terminal_ms=to_terminal,
                      server_events=times, polls=followed["polls"],
                      engines_started=sum(e["kind"] == "ENGINE_STARTED" for e in followed["events"]),
                      engines_finished=sum(e["kind"] == "ENGINE_FINISHED" for e in followed["events"]),
                      result_state=(followed["page"] or {}).get("result_state"),
                      status_final=(summary or {}).get("status"),
                      executions_complete=sum(bool(e.get("complete")) for e in (summary or {}).get("executions", [])),
                      placed_units=[e.get("placed_units") for e in (summary or {}).get("executions", [])],
                      release_status=released)
        if to_terminal is not None:
            metric("tiempo hasta TERMINAL (cliente)", to_terminal)
        _append_jsonl("e2_comparisons.jsonl", record)
        self.finish()


# ------------------------------------------ E3: confirmación concurrente
_e3 = {"shared": {}, "ready": defaultdict(Event), "go": defaultdict(lambda: GBarrier(2)),
       "done": defaultdict(lambda: GBarrier(2))}


class ConfirmUser(QueueUser):
    pool = "confirm"

    @task
    def confirm_twice(self):
        start_barrier().wait(timeout=180)
        session = self.index  # 0 = sesión que prepara la comparación, 1 = segunda sesión
        for rep in range(E3_REPS):
            if session == 0:
                self.prepare(rep)
            elif not _e3["ready"][rep].wait(POLL_DEADLINE_S):
                raise StopUser()
            shared = _e3["shared"][rep]
            if shared is None:
                _e3["done"][rep].wait()
                continue
            _e3["go"][rep].wait()
            sent, sent_mono = time.time(), time.monotonic()
            with self.client.post(f"{BASE}/{shared['comparison_id']}/confirm",
                                  json={"execution_id": shared["execution_id"], "expected_version": shared["version"]},
                                  headers={"Idempotency-Key": f"load-{RUN_ID}-r{rep}-s{session}"},
                                  name="POST /comparisons-v2/[id]/confirm", catch_response=True) as response:
                received_mono = time.monotonic()
                code = error_code(response)
                body = response.json() if response.status_code == 200 else None
                if response.status_code == 409 and code in ("ALREADY_CONFIRMED", "STATE_CONFLICT"):
                    response.success()  # la segunda confirmación debe rechazarse sin efecto
            receipt = (body or {}).get("receipt") or {}
            _append_jsonl("e3_confirmations.jsonl", dict(
                run_id=RUN_ID, rep=rep, session=session, comparison_id=shared["comparison_id"],
                order_id=shared["order_id"], execution_id=shared["execution_id"], method=shared["method"],
                sent=sent, response_ms=(received_mono - sent_mono) * 1000,
                status=response.status_code, code=code,
                materials_consumed=receipt.get("materials_consumed"),
                remnants_created=len(receipt.get("remnants_created") or [])))
            _e3["done"][rep].wait()
            if session == 0:
                # Lectura posterior: ¿la comparación confirmada se puede seguir consultando?
                readback = self.client.get(f"{BASE}/{shared['comparison_id']}", name="GET /comparisons-v2/[id] (después de confirmar)")
                _append_jsonl("e3_readback.jsonl", dict(rep=rep, comparison_id=shared["comparison_id"],
                                                        status=readback.status_code, code=error_code(readback)))
        self.finish()

    def prepare(self, rep):
        order_id = DATA["orders"]["confirm"][E3_OFFSET + rep]
        shared = None
        try:
            admission = self.admit(order_id, f"load-{RUN_ID}-prep{rep}")
            if admission["status"] != 201:
                return
            comparison_id = admission["body"]["comparison_id"]
            followed = self.follow(comparison_id)
            summary = self.summary(comparison_id)
            if followed["terminal_observed"] is None or summary is None:
                return
            eligible = [e for e in summary["executions"] if e.get("eligible")]
            chosen = next((e for e in eligible if e["method"] == "FF"), eligible[0] if eligible else None)
            if chosen is None:
                return
            response = self.client.put(f"{BASE}/{comparison_id}/selection",
                                       json={"execution_id": chosen["execution_id"], "expected_version": summary["version"]},
                                       name="PUT /comparisons-v2/[id]/selection")
            if response.status_code != 200:
                return
            shared = dict(comparison_id=comparison_id, order_id=order_id, execution_id=chosen["execution_id"],
                          method=chosen["method"], version=response.json()["version"])
        finally:
            _e3["shared"][rep] = shared
            _e3["ready"][rep].set()
