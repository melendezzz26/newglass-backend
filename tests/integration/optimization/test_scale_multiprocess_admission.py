"""Two real coordinators, one owned PostgreSQL, two disjoint synthetic orders."""
from functools import partial
import multiprocessing
from threading import Event

import pytest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.models import MovimientoInventario, Optimizacion, Plancha
from app.modules.optimization.infrastructure.comparison_v2_uow import SqlComparisonPersistenceUnitOfWork
from app.modules.optimization.infrastructure.in_process_admission import InProcessRunAdmission
from app.modules.optimization.presentation.comparison_supervision_v2 import ComparisonV2Coordinator
from app.modules.optimization.application.admission import OptimizerBusy
from .test_runs import prepared  # noqa: F401


@pytest.mark.parametrize('same_user,limit', [(False, 'active_runs'), (True, 'active_runs_per_user')])
def test_second_comparison_is_rejected_while_first_owns_process(session_factory, prepared, create_user, same_user, limit):
    actor, ids, _ = prepared
    other = actor if same_user else create_user('O73000002')
    coordinator = ComparisonV2Coordinator(partial(SqlComparisonPersistenceUnitOfWork, session_factory), InProcessRunAdmission())
    reached, release = Event(), Event()
    def sink(signal):
        if signal.kind == 'dispatching' and not reached.is_set():
            reached.set()
            release.wait(30)
    try:
        first = coordinator.start([ids[0]], actor, 'scale-same-process-first', sink=sink)
        assert reached.wait(30)
        with pytest.raises(OptimizerBusy) as rejected:
            coordinator.start([ids[1]], other, 'scale-same-process-second')
        assert rejected.value.code == 'OPTIMIZER_BUSY'
        assert rejected.value.details['limit']['name'] == limit
        assert rejected.value.details['request_accepted'] is False
        release.set()
        assert coordinator.job.closed.wait(90)
        with coordinator.uow() as tx:
            current = tx.comparisons_v2.get(first.comparison_id)
        assert current.terminal and current.supervisor_stopped
        with session_factory() as session:
            assert session.scalar(select(func.count()).select_from(Optimizacion)) == 1
            assert session.get(Plancha, 5).cantidad == 2
            assert session.scalar(select(func.count()).select_from(MovimientoInventario)) == 0
    finally:
        release.set()
        coordinator.shutdown()


def run_coordinator(url, actor, order_id, gate, messages):
    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=True)
    coordinator = ComparisonV2Coordinator(partial(SqlComparisonPersistenceUnitOfWork, factory), InProcessRunAdmission())
    announced = False
    def sink(signal):
        nonlocal announced
        if signal.kind == 'dispatching' and not announced:
            announced = True
            messages.put(('dispatching', order_id, signal.comparison_id))
            if not gate.wait(30):
                raise RuntimeError('parent did not release both coordinators')
    try:
        accepted = coordinator.start([order_id], actor, f'scale-two-process-{order_id}', sink=sink)
        if not coordinator.job.closed.wait(90):
            raise TimeoutError('comparison did not close')
        with coordinator.uow() as tx:
            current = tx.comparisons_v2.get(accepted.comparison_id)
        messages.put(('result', order_id, current.comparison_id, current.input_sha256,
                      current.terminal, current.supervisor_stopped,
                      [e.diagnostic.termination_reason for e in current.executions]))
    except BaseException as error:
        messages.put(('error', order_id, type(error).__name__, str(error)))
    finally:
        coordinator.shutdown()
        engine.dispose()


def test_two_processes_admit_comparisons_independently(session_factory, prepared):
    actor, ids, _ = prepared
    context = multiprocessing.get_context('spawn')
    gate, messages = context.Event(), context.Queue()
    url = session_factory.kw['bind'].url.render_as_string(hide_password=False)
    # The owned test database binds loopback only; never accept a remote target.
    assert session_factory.kw['bind'].url.host == '127.0.0.1'
    with session_factory() as session:
        before = session.get(Plancha, 5).cantidad
    processes = [context.Process(target=run_coordinator, args=(url, actor, order_id, gate, messages)) for order_id in ids]
    try:
        for process in processes:
            process.start()
        first = [messages.get(timeout=45), messages.get(timeout=45)]
        assert all(item[0] == 'dispatching' for item in first), first
        assert {item[1] for item in first} == set(ids)
        assert len({item[2] for item in first}) == 2
        gate.set()
        final = [messages.get(timeout=100), messages.get(timeout=100)]
        assert all(item[0] == 'result' for item in final), final
        assert all(item[4] and item[5] and item[3] and len(item[6]) == 6 for item in final)
        with session_factory() as session:
            assert session.scalar(select(func.count()).select_from(Optimizacion)) == 2
            assert session.get(Plancha, 5).cantidad == before
            assert session.scalar(select(func.count()).select_from(MovimientoInventario)) == 0
    finally:
        gate.set()
        for process in processes:
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join(10)
