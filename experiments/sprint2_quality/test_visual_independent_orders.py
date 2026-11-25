"""Focused local evidence using the production V2 API and standalone worker."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Barrier
import time

from sqlalchemy import update
from app.models import Configuracion, Pieza, Plancha
from tests.integration.postgres_support import isolated_postgres  # noqa: F401
from tests.integration.optimization.test_comparison_v2_http_restart import (  # noqa: F401
    worker_db, activate, accept, queue_state, summary, wait_for,
)


def test_independent_orders_use_durable_queue(worker_db):
    data = worker_db
    # Only the isolated r1_ database built by worker_db is touched.
    assert data.engine.url.host == '127.0.0.1'
    assert data.engine.url.username == 'r1_test' and data.engine.url.database.startswith('r1_')
    with data.sessions.begin() as db:
        db.execute(update(Plancha).values(ancho_mm=1000, alto_mm=1000, cantidad=2))
        db.execute(update(Configuracion).values(resolucion_raster_mm=10, margen_mm=10, separacion_mm=10))
        db.execute(update(Pieza).where(Pieza.id_pedido.in_([data.orders['A'], data.orders['O']])).values(
            cantidad=50, dimensiones={'width_mm': 100, 'height_mm': 100},
            geometria={'type': 'RECTANGULO', 'width_mm': 100, 'height_mm': 100}, area_mm2=10000))
    assert activate(data) == 0
    _, client = data.services.api('visual-api')
    data.services.worker('visual-worker')
    barrier = Barrier(2)
    started = time.monotonic()
    def stamp():
        return round(time.monotonic() - started, 3)
    def send(actor):
        barrier.wait(timeout=10)
        start = stamp()
        response = accept(client, data, actor, key=f'visual-independent-{actor}-01')
        return {'actor': actor, 'order_id': data.orders[actor], 'started_s': start,
                'received_s': stamp(), 'http_status': response.status_code, 'body': response.json()}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send, actor) for actor in ('A', 'O')]
        submissions = [future.result() for future in futures]
    assert all(item['http_status'] == 201 for item in submissions), submissions
    assert submissions[0]['order_id'] != submissions[1]['order_id']
    ids = {item['actor']: item['body']['comparison_id'] for item in submissions}
    assert ids['A'] != ids['O']
    timeline = []
    overlap = False
    automatic_advance = False
    deadline = time.monotonic() + 240
    previous = None
    while time.monotonic() < deadline:
        states = {actor: queue_state(client, data, actor, identifier) for actor, identifier in ids.items()}
        if states != previous:
            timeline.append({'elapsed_s': stamp(), 'states': states})
            previous = states
        if set(states.values()) == {'RUNNING', 'WAITING'}:
            overlap = True
            waiting_actor = next(actor for actor in states if states[actor] == 'WAITING')
        if overlap and states.get(waiting_actor) in ('RUNNING', 'TERMINAL'):
            automatic_advance = True
        if all(state == 'TERMINAL' for state in states.values()):
            break
        time.sleep(.1)
    assert overlap, timeline
    assert automatic_advance, timeline
    assert all(state == 'TERMINAL' for state in states.values()), timeline
    final = {actor: summary(client, data, actor, identifier) for actor, identifier in ids.items()}
    assert all(all(e['complete'] and e['placed_units'] == 50 for e in value['executions']) for value in final.values())
    record = {'type': 'current functional evidence; not a historical benchmark',
              'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'environment': 'one real Uvicorn API, one real standalone worker, disposable loopback PostgreSQL; durable pilot enabled',
              'fixture': 'two users, two independent synthetic 50-unit orders; no cut confirmation',
              'submissions': [{'actor': x['actor'], 'order_id': x['order_id'], 'comparison_id': x['body']['comparison_id'],
                               'http_status': x['http_status'], 'request_accepted': True,
                               'request_accepted_note': 'inferred from HTTP 201 and persisted comparison id',
                               'started_s': x['started_s'], 'received_s': x['received_s']} for x in submissions],
              'timeline': timeline, 'running_and_waiting_observed': overlap,
              'automatic_advance_without_resubmission': automatic_advance,
              'final': {actor: {'status': value['status'], 'terminal': value['terminal'],
                                'engines_complete': sum(bool(e['complete']) for e in value['executions']),
                                'units_placed_per_engine': [e['placed_units'] for e in value['executions']]}
                        for actor, value in final.items()}}
    target = Path(os.environ['VISUAL_CONCURRENCY_OUTPUT'])
    target.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('\n' + json.dumps(record, ensure_ascii=False))
