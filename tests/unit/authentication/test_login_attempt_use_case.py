"""EN-SEC-001: caso de uso de login con límite de intentos y un almacén en memoria."""
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.authentication.application.use_cases import authenticate_with_attempt_limit
from app.modules.authentication.domain.errors import (
    InactiveAccountError,
    InvalidCredentialsError,
    TooManyLoginAttemptsError,
)
from app.modules.authentication.domain.login_attempts import AttemptKey, AttemptPolicy
from app.modules.authentication.domain.user import UserAccount
from app.modules.authentication.domain.username import Username

T0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
POLICY = AttemptPolicy()
EXISTING = Username("F71234567")
MISSING = Username("Z99999999")
IP = "203.0.113.7"
RIGHT, WRONG = "correcta", "incorrecta"


class MemoryStore:
    def __init__(self):
        self.states, self.purges = {}, []

    def get(self, key):
        return self.states.get(key)

    def update(self, key, change, now):
        self.states[key] = change(self.states.get(key))
        return self.states[key]

    def clear(self, key):
        self.states.pop(key, None)

    def purge(self, older_than, now):
        self.purges.append((older_than, now))


class Reader:
    def __init__(self, *accounts):
        self.accounts = {a.username: a for a in accounts}

    def find_by_username(self, username):
        return self.accounts.get(username.value)

    def find_by_id(self, user_id):  # pragma: no cover - no se usa aquí
        return None


def account(username=EXISTING.value, active=True):
    return UserAccount(7, username, "Fabiola Quispe", "Administrador", active, password_hash=RIGHT)


class Verifier:
    def __init__(self):
        self.calls = 0

    def __call__(self, password, stored):
        self.calls += 1
        return password == stored


@pytest.fixture
def ctx():
    return {"store": MemoryStore(), "reader": Reader(account()), "verify": Verifier()}


def attempt(ctx, username=EXISTING, password=WRONG, ip=IP, now=T0):
    return authenticate_with_attempt_limit(
        username, password, ip, user_reader=ctx["reader"], verify_password=ctx["verify"],
        attempts=ctx["store"], policy=POLICY, now=now)


def outcome(ctx, **kwargs):
    try:
        attempt(ctx, **kwargs)
        return "ok"
    except TooManyLoginAttemptsError as error:
        return ("429", error.retry_after_seconds)
    except InvalidCredentialsError:
        return "401"


def test_four_failures_are_invalid_credentials_and_the_fifth_locks(ctx):
    results = [outcome(ctx) for _ in range(5)]
    assert results == ["401"] * 4 + [("429", 900)]


def test_during_the_lock_even_the_right_password_is_rejected_without_checking_it(ctx):
    for _ in range(5):
        outcome(ctx)
    calls = ctx["verify"].calls
    assert outcome(ctx, password=RIGHT, now=T0 + timedelta(minutes=5)) == ("429", 600)
    assert ctx["verify"].calls == calls


def test_lock_ends_when_the_lockout_expires(ctx):
    for _ in range(5):
        outcome(ctx)
    assert outcome(ctx, password=RIGHT, now=T0 + timedelta(minutes=15)) == "ok"


def test_success_clears_the_account_but_not_the_ip(ctx):
    for _ in range(4):
        outcome(ctx)
    assert outcome(ctx, password=RIGHT) == "ok"
    assert ctx["store"].get(AttemptKey.account(EXISTING.value)) is None
    assert ctx["store"].get(AttemptKey.ip(IP)).failures == 4


def test_missing_account_gets_exactly_the_same_outcomes(ctx):
    existing = [outcome(ctx, ip="198.51.100.1") for _ in range(6)]
    missing = [outcome(ctx, username=MISSING, ip="198.51.100.2") for _ in range(6)]
    assert existing == missing == ["401"] * 4 + [("429", 900)] * 2


def test_inactive_account_with_right_password_does_not_count(ctx):
    ctx["reader"] = Reader(account(active=False))
    for _ in range(6):
        with pytest.raises(InactiveAccountError):
            attempt(ctx, password=RIGHT)
    assert ctx["store"].get(AttemptKey.account(EXISTING.value)) is None


def test_ip_lock_blocks_other_accounts_from_the_same_ip(ctx):
    for index in range(5):
        outcome(ctx, username=Username(f"Q7000000{index}"))
    assert outcome(ctx, password=RIGHT) == ("429", 900)
    assert outcome(ctx, password=RIGHT, ip="198.51.100.9") == "ok"


def test_account_lock_applies_from_any_ip(ctx):
    for index in range(5):
        outcome(ctx, ip=f"198.51.100.{index}")
    assert outcome(ctx, password=RIGHT, ip="192.0.2.50") == ("429", 900)


def test_without_client_ip_only_the_account_is_limited(ctx):
    results = [outcome(ctx, ip=None) for _ in range(5)]
    assert results[-1] == ("429", 900)
    assert set(key.scope.value for key in ctx["store"].states) == {"account"}


def test_failures_trigger_an_opportunistic_purge_with_the_retention(ctx):
    outcome(ctx)
    assert ctx["store"].purges == [(T0 - timedelta(minutes=30), T0)]
