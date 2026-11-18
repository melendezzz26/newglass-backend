"""Regla de límite de intentos de inicio de sesión (EN-SEC-001).

Dominio puro: no conoce HTTP, la base ni el reloj del sistema. Recibe el
estado guardado de una clave (cuenta o IP) y el instante actual, y decide.

Regla (RN-01 a RN-04 de SPEC-EN-SEC-001):
- Una clave acumula fallos dentro de una ventana fija que empieza con el
  primer fallo. Si la ventana venció, el siguiente fallo abre una nueva.
- Al llegar al máximo de fallos dentro de la ventana, la clave queda
  bloqueada durante el tiempo de bloqueo y su contador vuelve a cero, de
  modo que al vencer el bloqueo dispone otra vez de todos los intentos.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from math import ceil

# Longitud máxima de una clave guardada: cubre un usuario (9) y una IPv6 (45).
MAX_KEY_LENGTH = 64


class AttemptScope(str, Enum):
    """Qué identifica la clave: una cuenta (usuario normalizado) o una IP de cliente."""

    ACCOUNT = "account"
    IP = "ip"


@dataclass(frozen=True)
class AttemptKey:
    scope: AttemptScope
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or len(self.value) > MAX_KEY_LENGTH:
            raise ValueError("La clave de intentos debe tener entre 1 y 64 caracteres.")

    @classmethod
    def account(cls, username: str) -> "AttemptKey":
        return cls(AttemptScope.ACCOUNT, username)

    @classmethod
    def ip(cls, address: str) -> "AttemptKey":
        return cls(AttemptScope.IP, address)


@dataclass(frozen=True)
class AttemptPolicy:
    """Umbral, ventana y bloqueo. Los valores vienen de la configuración."""

    max_failures: int = 5
    window: timedelta = timedelta(minutes=15)
    lockout: timedelta = timedelta(minutes=15)

    def __post_init__(self) -> None:
        if self.max_failures < 1:
            raise ValueError("max_failures debe ser al menos 1.")
        if self.window <= timedelta(0) or self.lockout <= timedelta(0):
            raise ValueError("La ventana y el bloqueo deben ser positivos.")

    @property
    def retention(self) -> timedelta:
        """Tiempo tras el cual un registro sin bloqueo vigente ya no influye en nada."""
        return self.window + self.lockout


@dataclass(frozen=True)
class AttemptState:
    """Estado guardado de una clave."""

    failures: int = 0
    window_started_at: datetime | None = None
    locked_until: datetime | None = None


def seconds_locked(state: AttemptState | None, now: datetime) -> int:
    """Segundos de bloqueo que quedan (0 si no hay bloqueo vigente).

    Se redondea hacia arriba para que Retry-After nunca invite a reintentar
    antes de tiempo.
    """
    if state is None or state.locked_until is None or state.locked_until <= now:
        return 0
    return max(1, ceil((state.locked_until - now).total_seconds()))


def register_failure(state: AttemptState | None, now: datetime, policy: AttemptPolicy) -> AttemptState:
    """Nuevo estado de la clave después de un intento fallido."""
    current = state or AttemptState()
    window_expired = (
        current.window_started_at is None or now - current.window_started_at >= policy.window
    )
    if window_expired:
        current = replace(current, failures=0, window_started_at=now)
    failures = current.failures + 1
    if failures >= policy.max_failures:
        # Bloqueo: el contador vuelve a cero para que, al vencer, haya de nuevo
        # todos los intentos disponibles (RN-03).
        return AttemptState(failures=0, window_started_at=None, locked_until=now + policy.lockout)
    return replace(current, failures=failures)
