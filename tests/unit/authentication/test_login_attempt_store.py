"""EN-SEC-001: almacén SQLAlchemy de intentos sobre la base SQLite de pruebas.

La atomicidad entre procesos (FOR UPDATE) se prueba contra PostgreSQL en
tests/integration/test_en_sec_001_login_attempts_postgres.py (corre en el CI).
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import IntentoLogin
from app.modules.authentication.domain.login_attempts import (
    AttemptKey,
    AttemptPolicy,
    AttemptState,
    register_failure,
)
from app.modules.authentication.infrastructure.sqlalchemy_login_attempt_store import (
    SqlAlchemyLoginAttemptStore,
)

T0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
ACCOUNT = AttemptKey.account("F71234567")
IP = AttemptKey.ip("203.0.113.7")
POLICY = AttemptPolicy()


@pytest.fixture
def store(db_session):
    return SqlAlchemyLoginAttemptStore(db_session)


def fail(store, key, now=T0):
    return store.update(key, lambda state: register_failure(state, now, POLICY), now)


def test_unknown_key_has_no_state(store):
    assert store.get(ACCOUNT) is None


def test_update_creates_then_accumulates_and_returns_aware_datetimes(store, session_factory):
    fail(store, ACCOUNT)
    state = fail(store, ACCOUNT, T0 + timedelta(seconds=5))
    assert state.failures == 2
    with session_factory() as other:  # otra sesión ve lo confirmado
        reread = SqlAlchemyLoginAttemptStore(other).get(ACCOUNT)
    assert reread == AttemptState(failures=2, window_started_at=T0, locked_until=None)
    assert reread.window_started_at.tzinfo is not None


def test_update_rereads_the_row_instead_of_the_session_copy(store, db_session, session_factory):
    fail(store, ACCOUNT)
    # La sesión conserva la fila cargada (con 1 fallo) mientras exista una referencia.
    held = db_session.scalars(select(IntentoLogin)).one()
    assert held.fallos == 1
    with session_factory() as other:  # otro proceso suma un fallo
        fail(SqlAlchemyLoginAttemptStore(other), ACCOUNT)
    # Sin relectura se partiría de la copia en memoria (1) y se perdería un fallo.
    assert fail(store, ACCOUNT).failures == 3


def test_keys_with_same_value_in_different_scopes_are_independent(store):
    fail(store, AttemptKey.account("X"))
    fail(store, AttemptKey.ip("X"))
    fail(store, AttemptKey.ip("X"))
    assert store.get(AttemptKey.account("X")).failures == 1
    assert store.get(AttemptKey.ip("X")).failures == 2


def test_lock_is_persisted(store):
    for _ in range(5):
        state = fail(store, IP)
    assert store.get(IP).locked_until == state.locked_until == T0 + timedelta(minutes=15)


def test_clear_removes_only_that_key(store):
    fail(store, ACCOUNT)
    fail(store, IP)
    store.clear(ACCOUNT)
    assert store.get(ACCOUNT) is None and store.get(IP) is not None
    store.clear(ACCOUNT)  # idempotente


def test_purge_removes_stale_rows_but_keeps_active_locks(store, db_session):
    old = T0 - timedelta(hours=2)
    fail(store, AttemptKey.ip("198.51.100.1"), old)  # vencido, sin bloqueo
    store.update(AttemptKey.ip("198.51.100.2"),  # viejo pero aún bloqueado
                 lambda _: AttemptState(locked_until=T0 + timedelta(minutes=1)), old)
    store.update(AttemptKey.ip("198.51.100.3"),  # bloqueo ya vencido
                 lambda _: AttemptState(locked_until=T0 - timedelta(minutes=1)), old)
    fail(store, ACCOUNT, T0)  # reciente
    store.purge(T0 - POLICY.retention, T0)
    remaining = set(db_session.scalars(select(IntentoLogin.clave)))
    assert remaining == {"198.51.100.2", ACCOUNT.value}
