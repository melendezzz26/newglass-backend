"""Casos de uso de autenticación. No conocen FastAPI ni SQLAlchemy."""
from datetime import datetime

from app.modules.authentication.domain.errors import (
    InactiveAccountError,
    InvalidCredentialsError,
    SessionNotValidError,
    TooManyLoginAttemptsError,
)
from app.modules.authentication.domain.login_attempts import (
    AttemptKey,
    AttemptPolicy,
    register_failure,
    seconds_locked,
)
from app.modules.authentication.domain.user import AuthenticatedUser
from app.modules.authentication.domain.username import Username

from .ports import LoginAttemptStore, PasswordVerifier, UserReader


def authenticate(
    username: Username,
    password: str,
    *,
    user_reader: UserReader,
    verify_password: PasswordVerifier,
) -> AuthenticatedUser:
    """Comprueba usuario y contraseña y devuelve la identidad autenticada."""
    account = user_reader.find_by_username(username)

    # Mismo error si la cuenta no existe o si la contraseña no coincide,
    # para no revelar qué usuarios están registrados.
    if account is None or not verify_password(password, account.password_hash):
        raise InvalidCredentialsError()

    # El estado solo se informa a quien ya demostró conocer la contraseña.
    if not account.is_active:
        raise InactiveAccountError()

    return account.to_authenticated_user()


def authenticate_with_attempt_limit(
    username: Username,
    password: str,
    client_ip: str | None,
    *,
    user_reader: UserReader,
    verify_password: PasswordVerifier,
    attempts: LoginAttemptStore,
    policy: AttemptPolicy,
    now: datetime,
) -> AuthenticatedUser:
    """Autentica aplicando el límite de intentos por cuenta y por IP (EN-SEC-001).

    - Si alguna de las dos claves está bloqueada, rechaza sin verificar la
      contraseña (RN-05): durante el bloqueo no se puede seguir probando.
    - Un fallo de credenciales suma en ambas claves; el que alcanza el
      umbral ya responde como bloqueado (D-04).
    - Una cuenta desactivada con la contraseña correcta no suma: no es un
      intento de adivinar la contraseña (D-05).
    - Un éxito limpia solo la clave de la cuenta, no la de la IP (RN-06, D-06).
    """
    account_key = AttemptKey.account(username.value)
    keys = [account_key]
    if client_ip:
        keys.append(AttemptKey.ip(client_ip))

    remaining = max(seconds_locked(attempts.get(key), now) for key in keys)
    if remaining:
        raise TooManyLoginAttemptsError(remaining)

    try:
        user = authenticate(
            username, password, user_reader=user_reader, verify_password=verify_password
        )
    except InvalidCredentialsError:
        states = [
            attempts.update(key, lambda state: register_failure(state, now, policy), now)
            for key in keys
        ]
        # Limpieza oportunista: los registros vencidos no influyen en la regla.
        attempts.purge(now - policy.retention, now)
        remaining = max(seconds_locked(state, now) for state in states)
        if remaining:
            raise TooManyLoginAttemptsError(remaining) from None
        raise

    attempts.clear(account_key)
    return user


def get_active_user(user_id: int, *, user_reader: UserReader) -> AuthenticatedUser:
    """Confirma que la cuenta de una sesión abierta sigue existiendo y activa.

    Devuelve los datos leídos ahora, de modo que el rol es el vigente en la
    base y no el que tenía la cuenta cuando se emitió el token.
    """
    account = user_reader.find_by_id(user_id)
    if account is None or not account.is_active:
        raise SessionNotValidError()
    return account.to_authenticated_user()
