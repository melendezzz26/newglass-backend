"""Contratos que la aplicación necesita y que la infraestructura implementa."""
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from app.modules.authentication.domain.login_attempts import AttemptKey, AttemptState
from app.modules.authentication.domain.user import UserAccount
from app.modules.authentication.domain.username import Username


class UserReader(Protocol):
    """Lectura de cuentas. La aplicación no sabe si detrás hay SQL u otra cosa."""

    def find_by_username(self, username: Username) -> UserAccount | None:
        """Cuenta con ese usuario exacto y un rol existente, o None."""
        ...

    def find_by_id(self, user_id: int) -> UserAccount | None:
        """Cuenta con ese identificador y un rol existente, o None."""
        ...

class PermissionReader(Protocol):
    """Lectura de los permisos asignados a un rol (S1-RBAC).

    La fuente de verdad es la base: se consulta en cada petición, sin caché,
    para que un cambio de permisos rija en la siguiente petición (RN-02).
    """

    def role_has_permission(self, role: str, permission: str) -> bool:
        """True si el rol con ese nombre tiene asignado el permiso con ese código."""
        ...

    def permissions_for_role(self, role: str) -> frozenset[str]:
        """Códigos de los permisos asignados al rol con ese nombre."""
        ...

class LoginAttemptStore(Protocol):
    """Estado persistente de los intentos fallidos por clave (EN-SEC-001).

    Es compartido entre procesos: la implementación real usa PostgreSQL. La
    regla vive en el dominio; el almacén solo garantiza que cada cambio de
    estado sea atómico aunque varios procesos fallen a la vez.
    """

    def get(self, key: AttemptKey) -> AttemptState | None:
        """Estado guardado de la clave, o None si no tiene intentos registrados."""
        ...

    def update(
        self,
        key: AttemptKey,
        change: Callable[[AttemptState | None], AttemptState],
        now: datetime,
    ) -> AttemptState:
        """Aplica ``change`` al estado actual de forma atómica y devuelve el nuevo estado.

        ``now`` queda como instante de la última actividad de la clave.
        """
        ...

    def clear(self, key: AttemptKey) -> None:
        """Elimina los intentos registrados de la clave."""
        ...

    def purge(self, older_than: datetime, now: datetime) -> None:
        """Borra registros sin actividad desde ``older_than`` y sin bloqueo vigente en ``now``."""
        ...


# Recibe la contraseña escrita y el hash guardado; devuelve si coinciden.
PasswordVerifier = Callable[[str, str], bool]
