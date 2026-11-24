"""Real loopback Uvicorn servers and an owned PostgreSQL; no confirmations."""
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Barrier, Event, Thread
import time

import httpx
from sqlalchemy import func, select

from app.models import MovimientoInventario, Plancha
from app.modules.orders.infrastructure.repositories import SQLAlchemyOrderRepository
from app.shared.security import create_access_token
from .test_runs import prepared  # noqa: F401

BASE = '/api/optimization/comparisons-v2'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class TreeSampler:
    """Linux procfs sampling; CPU is a lower bound when short-lived PIDs vanish."""
    def __init__(self, roots, interval=.02):
        self.roots, self.interval = roots, interval
        self.stop = Event()
        self.peak = 0
        self.root_peak = [0] * len(roots)
        self.cpu = {}
        self.first = {}
        self.owner = {}
        self.max_processes = 0
        self.samples = 0
        self.ticks = os.sysconf('SC_CLK_TCK')
        self.page = os.sysconf('SC_PAGE_SIZE')

    def sample(self):
        processes = {}
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                raw = (path / 'stat').read_text()
                values = raw[raw.rfind(')') + 2:].split()
                processes[int(path.name)] = (int(values[1]), int(values[11]), int(values[12]),
                                             int(values[19]), max(0, int(values[21])) * self.page)
            except (OSError, ValueError, IndexError):
                continue
        root_totals = []
        all_pids = set()
        owners = {}
        for root_index, root in enumerate(self.roots):
            members = {root}
            while True:
                children = {pid for pid, data in processes.items() if data[0] in members}
                expanded = members | children
                if expanded == members:
                    break
                members = expanded
            all_pids.update(members)
            owners.update({pid: root_index for pid in members})
            root_totals.append(sum(processes[pid][4] for pid in members if pid in processes))
        for pid in all_pids:
            if pid not in processes:
                continue
            _, user, system, start, _ = processes[pid]
            key = (pid, start)
            self.first.setdefault(key, (user, system) if pid in self.roots else (0, 0))
            self.owner.setdefault(key, owners[pid])
            self.cpu[key] = (user, system)
        self.peak = max(self.peak, sum(root_totals))
        self.root_peak = [max(old, new) for old, new in zip(self.root_peak, root_totals)]
        self.max_processes = max(self.max_processes, len(all_pids & processes.keys()))
        self.samples += 1

    def __enter__(self):
        self.sample()
        def loop():
            while not self.stop.wait(self.interval):
                self.sample()
        self.thread = Thread(target=loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()
        self.sample()

    def result(self):
        u = sum(max(0, last[0] - self.first[key][0]) for key, last in self.cpu.items()) / self.ticks
        s = sum(max(0, last[1] - self.first[key][1]) for key, last in self.cpu.items()) / self.ticks
        per_user = [sum(max(0, last[0] - self.first[key][0]) for key, last in self.cpu.items()
                        if self.owner[key] == i) / self.ticks for i in range(len(self.roots))]
        per_system = [sum(max(0, last[1] - self.first[key][1]) for key, last in self.cpu.items()
                          if self.owner[key] == i) / self.ticks for i in range(len(self.roots))]
        return dict(cpu_user_s=u, cpu_system_s=s, cpu_total_s=u+s,
                    per_server_cpu_user_s=per_user, per_server_cpu_system_s=per_system,
                    peak_rss_mb=self.peak / 1048576,
                    per_server_peak_rss_mb=[x / 1048576 for x in self.root_peak],
                    max_processes=self.max_processes, samples=self.samples,
                    sampling_interval_ms=self.interval*1000)


def start_server(url):
    port = free_port()
    env = dict(os.environ, DATABASE_URL=url, PYTHON_DOTENV_DISABLED='1')
    log = tempfile.TemporaryFile()
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1',
                                '--port', str(port), '--workers', '1', '--log-level', 'error'],
                               env=env, stdout=log, stderr=log)
    endpoint = f'http://127.0.0.1:{port}'
    for _ in range(200):
        if process.poll() is not None:
            log.seek(0)
            raise RuntimeError(log.read().decode(errors='replace')[-3000:])
        try:
            if httpx.get(endpoint + '/health/ready', timeout=.5).status_code == 200:
                return process, endpoint, log
        except httpx.HTTPError:
            pass
        time.sleep(.05)
    raise TimeoutError('server readiness timed out')


def new_orders(session_factory, actor, count):
    piece = dict(tipo_forma='RECTANGULO', cantidad=2, dimensiones={'width_mm': 2, 'height_mm': 2},
                 geometria={'type': 'RECTANGULO', 'width_mm': 2, 'height_mm': 2}, id_tipo_vidrio=7,
                 espesor_mm=6, area_mm2=4)
    with session_factory() as session:
        repo = SQLAlchemyOrderRepository(session)
        return [repo.create_order(actor, [piece], id_cliente=41) for _ in range(count)]


def test_http_two_servers_resource_and_admission(session_factory, prepared, create_user):
    actor, _, _ = prepared
    other = create_user('O73000002')
    headers = lambda who, name: {'Authorization': 'Bearer ' + create_access_token(who, name, 'Operario')}
    first_headers, other_headers = headers(actor, 'O73000001'), headers(other, 'O73000002')
    url = session_factory.kw['bind'].url
    assert url.host == '127.0.0.1' and url.username == 'r1_test'
    servers = []
    rows = []
    details = []
    with session_factory() as session:
        stock_before = session.get(Plancha, 5).cantidad
    try:
        servers = [start_server(url.render_as_string(hide_password=False)) for _ in range(2)]
        for scenario, indexes, users in [('R1', [0], [first_headers]),
                                         ('R2_distinct', [0, 0], [first_headers, other_headers]),
                                         ('R2_same', [0, 0], [first_headers, first_headers]),
                                         ('R3', [0, 1], [first_headers, first_headers])]:
            orders = new_orders(session_factory, actor, len(indexes))
            gate = Barrier(len(indexes))
            def send(i):
                gate.wait(timeout=20)
                began = time.monotonic()
                response = httpx.post(servers[indexes[i]][1] + BASE, json={'order_ids': [orders[i]]},
                    headers={**users[i], 'Idempotency-Key': f'scale-{scenario}-{i}-key'}, timeout=30)
                return dict(status=response.status_code, body=response.json(),
                            request_wall_ms=(time.monotonic()-began)*1000, server=indexes[i])
            roots = [servers[i][0].pid for i in sorted(set(indexes))]
            began = time.monotonic()
            with TreeSampler(roots) as sampler:
                with ThreadPoolExecutor(max_workers=len(indexes)) as pool:
                    responses = list(pool.map(send, range(len(indexes))))
                accepted = [r for r in responses if r['status'] == 201]
                def wait_result(response):
                    item = response['body']
                    for _ in range(1200):
                        current = httpx.get(servers[response['server']][1] + BASE + f"/{item['comparison_id']}",
                                            headers=users[responses.index(response)], timeout=10)
                        if current.status_code == 200 and current.json()['terminal'] and current.json()['supervisor_stopped']:
                            return dict(summary=current.json(), observed_wall_ms=(time.monotonic()-began)*1000)
                        time.sleep(.05)
                    raise TimeoutError(f'{scenario} failed to finish: {current.status_code}')
                with ThreadPoolExecutor(max_workers=max(1, len(accepted))) as pool:
                    finished = list(pool.map(wait_result, accepted))
                final = [item['summary'] for item in finished]
            elapsed = (time.monotonic()-began)*1000
            with session_factory() as session:
                inventory_ok = (session.get(Plancha, 5).cantidad == stock_before and
                    session.scalar(select(func.count()).select_from(MovimientoInventario)) == 0)
            assert inventory_ok
            # Query a terminal comparison through the other HTTP process, without recovery mutation.
            cross = None
            if scenario == 'R3' and final:
                cross_response = httpx.get(servers[1][1] + BASE + f"/{final[0]['comparison_id']}",
                                           headers=first_headers, timeout=10)
                cross = dict(status=cross_response.status_code,
                             same_snapshot=cross_response.status_code == 200 and
                             cross_response.json()['input_sha256'] == final[0]['input_sha256'])
            metrics = sampler.result()
            errors = [r['body'].get('detail') for r in responses if r['status'] != 201]
            rows.append(dict(scenario=scenario, server_count=len(roots), requests=len(indexes),
                accepted=len(accepted), rejected=len(indexes)-len(accepted),
                rejection_code=json.dumps(errors), wall_time_ms=round(elapsed, 3),
                final_status=','.join(x['status'] for x in final),
                snapshot_ok=all(len(x['input_sha256']) == 64 for x in final),
                persistence_ok=all(x['terminal'] and x['supervisor_stopped'] for x in final),
                backend_sha=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                environment='WSL2/Linux, PostgreSQL 16 loopback', **metrics))
            details.append(dict(scenario=scenario, responses=responses, final=final,
                                job_observed_wall_ms=[round(x['observed_wall_ms'], 3) for x in finished],
                                cross_server=cross))
            print('OBSERVED', scenario, [r['status'] for r in responses], flush=True)
        destination = os.environ.get('NEWGLASS_CONCURRENCY_EVIDENCE_DIR')
        if destination:
            output = Path(destination)
            output.mkdir(parents=True, exist_ok=True)
            with (output/'concurrency_raw_results.csv').open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)
            (output/'concurrency_details.json').write_text(json.dumps(details, indent=2)+'\n')
        assert rows[0]['accepted'] == 1
        assert rows[1]['accepted'] == rows[2]['accepted'] == 1
        assert rows[3]['accepted'] == 2
        assert details[3]['cross_server']['same_snapshot']
    finally:
        for process, _, log in servers:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
            log.close()


def test_cross_server_cancel_and_controlled_owner_stop(session_factory, prepared):
    actor, ids, _ = prepared
    token = create_access_token(actor, 'O73000001', 'Operario')
    headers = {'Authorization': 'Bearer ' + token}
    url = session_factory.kw['bind'].url
    assert url.host == '127.0.0.1' and url.username == 'r1_test'
    servers = []
    evidence = {}
    try:
        servers = [start_server(url.render_as_string(hide_password=False)) for _ in range(2)]
        a, b = servers[0][1], servers[1][1]
        first = httpx.post(a + BASE, json={'order_ids': [ids[0]]},
                           headers={**headers, 'Idempotency-Key': 'cross-cancel-start'}, timeout=20)
        assert first.status_code == 201, first.text
        item = first.json()
        target = f"{BASE}/{item['comparison_id']}"
        response = httpx.post(b + target + '/cancel', json={'expected_version': item['version']},
                              headers={**headers, 'Idempotency-Key': 'cross-cancel-request'}, timeout=20)
        evidence['cross_cancel'] = dict(status=response.status_code, body=response.json())
        assert response.status_code == 503 and response.json()['detail']['code'] == 'RECOVERY_BLOCKED'
        for _ in range(300):
            current = httpx.get(a + target, headers=headers, timeout=10)
            if current.status_code == 200 and current.json()['terminal'] and current.json()['supervisor_stopped']:
                break
            time.sleep(.05)
        evidence['after_cross_cancel_owner'] = dict(status=current.status_code, body=current.json())
        assert current.status_code == 200 and current.json()['terminal']
        assert current.json()['cancellation_status'] == 'NONE'
        # A fresh order isolates the interruption experiment from the prior run.
        order = new_orders(session_factory, actor, 1)[0]
        started = httpx.post(a + BASE, json={'order_ids': [order]},
                             headers={**headers, 'Idempotency-Key': 'owner-stop-start'}, timeout=20)
        assert started.status_code == 201, started.text
        interrupted = started.json()
        owner = servers[0][0]
        owner.kill()
        owner.wait(timeout=10)
        target = f"{BASE}/{interrupted['comparison_id']}"
        observations = []
        for _ in range(100):
            result = httpx.get(b + target, headers=headers, timeout=10)
            observations.append(dict(status=result.status_code, body=result.json()))
            if result.status_code == 200 and result.json()['terminal']:
                break
            time.sleep(.1)
        evidence['controlled_stop'] = dict(comparison_id=interrupted['comparison_id'],
                                           initial_snapshot=interrupted['input_sha256'],
                                           observations=observations)
        assert result.status_code == 200 and result.json()['terminal'] and result.json()['supervisor_stopped']
        assert result.json()['input_sha256'] == interrupted['input_sha256']
        assert result.json()['status'] == 'FALLIDA'
        with session_factory() as session:
            evidence['inventory_unchanged'] = (session.get(Plancha, 5).cantidad == 2 and
                session.scalar(select(func.count()).select_from(MovimientoInventario)) == 0)
        assert evidence['inventory_unchanged']
    finally:
        for process, _, log in servers:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
            log.close()
        destination = os.environ.get('NEWGLASS_CONCURRENCY_EVIDENCE_DIR')
        if destination:
            output = Path(destination)
            output.mkdir(parents=True, exist_ok=True)
            (output/'recovery_cancel_observations.json').write_text(json.dumps(evidence, indent=2)+'\n')
