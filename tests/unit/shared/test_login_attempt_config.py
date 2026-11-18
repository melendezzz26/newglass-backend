"""EN-SEC-001: configuración del límite de intentos desde el entorno."""
import pytest

from app.shared.config import login_attempt_limits

NAMES = ("LOGIN_MAX_FAILED_ATTEMPTS", "LOGIN_ATTEMPT_WINDOW_MINUTES", "LOGIN_LOCKOUT_MINUTES")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_five_attempts_fifteen_minutes_and_fifteen_minutes():
    assert login_attempt_limits() == (5, 15, 15)


def test_values_come_from_the_environment(monkeypatch):
    for name, value in zip(NAMES, ("3", "10", "30")):
        monkeypatch.setenv(name, value)
    assert login_attempt_limits() == (3, 10, 30)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("value", ["0", "-1", "cinco", "1.5", ""])
def test_invalid_values_fail_with_the_variable_name(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        login_attempt_limits()


@pytest.mark.parametrize("name", NAMES[1:])
def test_minutes_beyond_the_date_range_are_rejected(monkeypatch, name):
    monkeypatch.setenv(name, str(10**12))
    with pytest.raises(RuntimeError, match=name):
        login_attempt_limits()
