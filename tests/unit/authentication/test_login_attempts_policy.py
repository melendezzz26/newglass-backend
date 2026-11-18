"""EN-SEC-001: regla de dominio del límite de intentos (sin base ni HTTP)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.authentication.domain.login_attempts import (
    AttemptKey,
    AttemptPolicy,
    AttemptScope,
    AttemptState,
    register_failure,
    seconds_locked,
)

T0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
POLICY = AttemptPolicy()  # 5 fallos / 15 min / 15 min


def fail(times, *, start=T0, step=timedelta(seconds=10), policy=POLICY, state=None):
    now = start
    for _ in range(times):
        state = register_failure(state, now, policy)
        now += step
    return state, now


def test_default_policy_matches_the_team_decision():
    assert (POLICY.max_failures, POLICY.window, POLICY.lockout) == (
        5, timedelta(minutes=15), timedelta(minutes=15))


def test_failures_below_the_threshold_do_not_lock():
    state, now = fail(4)
    assert state.failures == 4
    assert state.window_started_at == T0
    assert seconds_locked(state, now) == 0


def test_fifth_failure_locks_for_the_lockout_and_resets_the_counter():
    state, _ = fail(5)
    fifth = T0 + timedelta(seconds=40)
    assert state.locked_until == fifth + timedelta(minutes=15)
    assert state.failures == 0 and state.window_started_at is None
    assert seconds_locked(state, fifth) == 900


def test_lock_expires_exactly_at_locked_until():
    state, _ = fail(5)
    assert seconds_locked(state, state.locked_until - timedelta(milliseconds=1)) == 1
    assert seconds_locked(state, state.locked_until) == 0


def test_retry_after_is_rounded_up_and_never_zero_while_locked():
    state = AttemptState(locked_until=T0 + timedelta(seconds=10, milliseconds=200))
    assert seconds_locked(state, T0) == 11
    assert seconds_locked(None, T0) == 0


def test_window_expiry_starts_a_new_count():
    state, _ = fail(4)
    later = T0 + timedelta(minutes=15)
    state = register_failure(state, later, POLICY)
    assert state.failures == 1 and state.window_started_at == later
    assert seconds_locked(state, later) == 0


def test_failures_spread_beyond_the_window_never_lock():
    state, now = fail(20, step=timedelta(minutes=4))  # 4 por ventana de 15 min
    assert seconds_locked(state, now) == 0


def test_after_the_lock_expires_all_attempts_are_available_again():
    state, _ = fail(5)
    after = state.locked_until
    state, now = fail(4, start=after, state=state)
    assert seconds_locked(state, now) == 0
    state = register_failure(state, now, POLICY)
    assert seconds_locked(state, now) == 900


def test_failure_registered_during_an_active_lock_keeps_the_lock():
    # Dos procesos que fallan a la vez pueden registrar un fallo sobre un estado ya bloqueado.
    state, _ = fail(5)
    during = T0 + timedelta(minutes=1)
    state2 = register_failure(state, during, POLICY)
    assert state2.locked_until == state.locked_until


def test_custom_policy_is_respected():
    policy = AttemptPolicy(max_failures=2, window=timedelta(minutes=1), lockout=timedelta(minutes=3))
    state, _ = fail(2, policy=policy)
    assert seconds_locked(state, T0 + timedelta(seconds=10)) == 180


@pytest.mark.parametrize("kwargs", [
    {"max_failures": 0},
    {"window": timedelta(0)},
    {"lockout": timedelta(seconds=-1)},
])
def test_invalid_policy_is_rejected(kwargs):
    with pytest.raises(ValueError):
        AttemptPolicy(**kwargs)


def test_retention_covers_window_and_lockout():
    assert POLICY.retention == timedelta(minutes=30)


@pytest.mark.parametrize("value", ["", "x" * 65, None])
def test_invalid_key_is_rejected(value):
    with pytest.raises(ValueError):
        AttemptKey(AttemptScope.IP, value)


def test_key_constructors():
    assert AttemptKey.account("F71234567") == AttemptKey(AttemptScope.ACCOUNT, "F71234567")
    assert AttemptKey.ip("2001:db8::1").scope is AttemptScope.IP
    assert AttemptScope.ACCOUNT.value == "account" and AttemptScope.IP.value == "ip"
