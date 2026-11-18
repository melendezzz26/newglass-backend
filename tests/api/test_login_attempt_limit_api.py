"""EN-SEC-001: límite de intentos de POST /api/auth/login de extremo a extremo.

Usa la base SQLite de pruebas (tabla intentos_login incluida) y sustituye el
reloj y la IP del cliente para simular el paso del tiempo y varios orígenes.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import IntentoLogin
from app.modules.authentication.presentation.dependencies import get_client_ip, get_clock
from main import app

T0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
EXISTING = "F70000101"
OTHER = "A70000102"
MISSING = "Z70000199"
WRONG = "contrasena-incorrecta"
INVALID = "Usuario o contraseña incorrectos."
LOCKED = "Demasiados intentos fallidos. Espera unos minutos antes de volver a intentarlo."


@pytest.fixture
def env(client, create_user, monkeypatch):
    for name in ("LOGIN_MAX_FAILED_ATTEMPTS", "LOGIN_ATTEMPT_WINDOW_MINUTES", "LOGIN_LOCKOUT_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    create_user(EXISTING, "Administrador")
    create_user(OTHER, "Operario")
    state = {"now": T0, "ip": "203.0.113.10"}
    app.dependency_overrides[get_clock] = lambda: (lambda: state["now"])
    app.dependency_overrides[get_client_ip] = lambda: state["ip"]
    try:
        yield state
    finally:
        app.dependency_overrides.pop(get_clock, None)
        app.dependency_overrides.pop(get_client_ip, None)


def login(client, username, password, **kwargs):
    return client.post("/api/auth/login", json={"username": username, "password": password}, **kwargs)


def fail(client, username=EXISTING, times=1):
    return [login(client, username, WRONG) for _ in range(times)]


def summary(response):
    return response.status_code, response.json()["detail"], response.headers.get("retry-after")


def test_fifth_failure_locks_with_429_and_retry_after(client, env):
    responses = fail(client, times=5)
    assert [r.status_code for r in responses[:4]] == [401] * 4
    assert all(r.json()["detail"] == INVALID for r in responses[:4])
    assert summary(responses[4]) == (429, LOCKED, "900")


def test_locked_account_rejects_the_right_password(client, env, password):
    fail(client, times=5)
    env["now"] = T0 + timedelta(minutes=10)
    response = login(client, EXISTING, password)
    assert summary(response) == (429, LOCKED, "300")
    assert "access_token" not in response.json()


def test_lock_ends_when_the_lockout_expires(client, env, password):
    fail(client, times=5)
    env["now"] = T0 + timedelta(minutes=15)
    response = login(client, EXISTING, password)
    assert response.status_code == 200 and response.json()["access_token"]


def test_failures_outside_the_window_do_not_accumulate(client, env):
    fail(client, times=4)
    env["now"] = T0 + timedelta(minutes=15)
    assert [r.status_code for r in fail(client, times=4)] == [401] * 4


def test_successful_login_resets_the_account_counter(client, env, password):
    fail(client, times=4)
    assert login(client, EXISTING, password).status_code == 200
    env["ip"] = "198.51.100.20"  # otra IP: aísla el contador de la cuenta
    assert [r.status_code for r in fail(client, times=4)] == [401] * 4
    assert fail(client)[0].status_code == 429


def test_missing_account_gets_identical_responses(client, env):
    existing = [summary(r) for r in fail(client, EXISTING, times=6)]
    env["ip"] = "198.51.100.30"
    missing = [summary(r) for r in fail(client, MISSING, times=6)]
    assert existing == missing
    assert existing[-2:] == [(429, LOCKED, "900")] * 2


def test_locked_response_body_and_headers_do_not_reveal_the_account(client, env):
    locked_existing = fail(client, EXISTING, times=5)[-1]
    env["ip"] = "198.51.100.31"
    locked_missing = fail(client, MISSING, times=5)[-1]
    assert locked_existing.content == locked_missing.content
    assert locked_existing.headers.get("retry-after") == locked_missing.headers.get("retry-after")


def test_ip_lock_blocks_another_valid_account_from_the_same_ip(client, env, password):
    for index in range(5):
        fail(client, f"Q7000010{index}")
    assert summary(login(client, OTHER, password)) == (429, LOCKED, "900")
    env["ip"] = "198.51.100.40"
    assert login(client, OTHER, password).status_code == 200


def test_account_lock_follows_the_account_across_ips(client, env, password):
    for index in range(5):
        env["ip"] = f"198.51.100.{50 + index}"
        fail(client)
    env["ip"] = "192.0.2.99"
    assert summary(login(client, EXISTING, password)) == (429, LOCKED, "900")


def test_x_forwarded_for_sent_by_the_client_is_ignored(client, create_user, password):
    # Sin sustituir get_client_ip: la app usa la IP de la conexión, nunca la cabecera.
    create_user(EXISTING, "Administrador")
    for index in range(5):
        login(client, f"Q7000020{index}", WRONG, headers={"X-Forwarded-For": f"10.9.8.{index}"})
    response = login(client, EXISTING, password, headers={"X-Forwarded-For": "10.9.9.9"})
    assert response.status_code == 429


def test_username_is_normalized_before_counting(client, env):
    variants = [EXISTING.lower(), f"  {EXISTING}  ", EXISTING, EXISTING.lower(), EXISTING]
    responses = [login(client, v, WRONG) for v in variants]
    assert responses[-1].status_code == 429


def test_inactive_account_with_right_password_is_not_counted(client, env, create_user, password):
    create_user("I70000105", "Operario", active=False)
    assert {login(client, "I70000105", password).status_code for _ in range(6)} == {403}


def test_invalid_username_format_is_422_and_not_counted(client, env, session_factory):
    assert {login(client, "no-valido", WRONG).status_code for _ in range(6)} == {422}
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(IntentoLogin)) == 0


def test_limits_are_configurable_by_environment(client, env, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILED_ATTEMPTS", "2")
    monkeypatch.setenv("LOGIN_ATTEMPT_WINDOW_MINUTES", "1")
    monkeypatch.setenv("LOGIN_LOCKOUT_MINUTES", "3")
    responses = fail(client, times=2)
    assert responses[0].status_code == 401
    assert summary(responses[1]) == (429, LOCKED, "180")


def test_state_is_persisted_in_the_database(client, env, session_factory):
    fail(client, times=2)
    with session_factory() as db:
        rows = {(r.alcance, r.clave): r.fallos for r in db.scalars(select(IntentoLogin))}
    assert rows == {("account", EXISTING): 2, ("ip", "203.0.113.10"): 2}


def test_login_stays_public_and_documents_429():
    operation = app.openapi()["paths"]["/api/auth/login"]["post"]
    assert "429" in operation["responses"]
    assert "security" not in operation
