"""Reglas compartidas del arnés: solo loopback y rutas de salida."""
from ipaddress import ip_address
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

LOOPBACK_NAMES = {"localhost"}


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def require_loopback_url(url: str) -> None:
    """RN-01: la carga solo puede ir a 127.0.0.1 (nunca Heroku, Pages ni Supabase)."""
    host = urlsplit(url).hostname
    if not is_loopback(host):
        raise SystemExit(f"Destino rechazado: la carga solo se permite contra loopback, no contra {host!r}.")


def require_loopback_database(url: str) -> None:
    """RN-01: la base de datos debe ser el clúster temporal en loopback."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or not is_loopback(parsed.host):
        raise SystemExit("Base rechazada: el arnés solo usa el PostgreSQL temporal en loopback.")


# Pools de cuentas sintéticas: prefijo de usuario (letra + 8 dígitos) y rol.
ACCOUNT_POOLS = {
    "admin": ("A", "Administrador"),
    "almacenero": ("L", "Almacenero"),
    "operario": ("O", "Operario"),
    "comparison": ("C", "Operario"),
    "confirm": ("K", "Operario"),
}


def username(pool: str, index: int) -> str:
    prefix = ACCOUNT_POOLS[pool][0]
    return f"{prefix}{70000000 + index:08d}"
