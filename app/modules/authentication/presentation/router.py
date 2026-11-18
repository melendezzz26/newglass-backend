"""Rutas de autenticación: traducen HTTP a casos de uso y errores a códigos."""
from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.modules.authentication.application.ports import (
    LoginAttemptStore,
    PermissionReader,
    UserReader,
)
from app.modules.authentication.application.use_cases import authenticate_with_attempt_limit
from app.modules.authentication.domain.errors import (
    InactiveAccountError,
    InvalidCredentialsError,
    InvalidUsernameError,
    TooManyLoginAttemptsError,
)
from app.modules.authentication.domain.login_attempts import AttemptPolicy
from app.modules.authentication.domain.user import AuthenticatedUser
from app.modules.authentication.domain.username import Username
from app.shared.security import create_access_token, verify_password

from .dependencies import (
    get_client_ip,
    get_clock,
    get_current_user,
    get_login_attempt_policy,
    get_login_attempt_store,
    get_permission_reader,
    get_user_reader,
)
from .schemas import LoginRequest, LoginResponse, PublicUser, SessionInfo

router = APIRouter(prefix="/api/auth", tags=["Autenticación"])

# Mensaje único para no revelar si el usuario existe.
INVALID_CREDENTIALS_MESSAGE = "Usuario o contraseña incorrectos."
INACTIVE_ACCOUNT_MESSAGE = "Tu cuenta está desactivada. Pide al administrador que la active."
# EN-SEC-001: mismo texto para cuenta existente o inexistente, y para bloqueo
# por cuenta o por IP. No dice cuál de las dos claves está bloqueada.
TOO_MANY_ATTEMPTS_MESSAGE = (
    "Demasiados intentos fallidos. Espera unos minutos antes de volver a intentarlo."
)


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={429: {"description": "Demasiados intentos fallidos; ver Retry-After"}},
)
def login(
    credentials: LoginRequest,
    user_reader: UserReader = Depends(get_user_reader),
    permission_reader: PermissionReader = Depends(get_permission_reader),
    attempts: LoginAttemptStore = Depends(get_login_attempt_store),
    policy: AttemptPolicy = Depends(get_login_attempt_policy),
    clock: Callable[[], datetime] = Depends(get_clock),
    client_ip: str | None = Depends(get_client_ip),
):
    try:
        # Si el formato no es válido se responde aquí, sin consultar la base.
        username = Username.parse(credentials.username)
    except InvalidUsernameError as error:
        raise HTTPException(422, str(error))

    try:
        user = authenticate_with_attempt_limit(
            username,
            credentials.password,
            client_ip,
            user_reader=user_reader,
            verify_password=verify_password,
            attempts=attempts,
            policy=policy,
            now=clock(),
        )
    except TooManyLoginAttemptsError as error:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            TOO_MANY_ATTEMPTS_MESSAGE,
            headers={"Retry-After": str(error.retry_after_seconds)},
        )
    except InvalidCredentialsError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS_MESSAGE)
    except InactiveAccountError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, INACTIVE_ACCOUNT_MESSAGE)

    return LoginResponse(
        access_token=create_access_token(user.user_id, user.username, user.role),
        usuario=PublicUser(
            id_usuario=user.user_id,
            username=user.username,
            nombre=user.full_name,
            rol=user.role,
            permisos=sorted(permission_reader.permissions_for_role(user.role)),
        ),
    )


@router.get("/me", response_model=SessionInfo)
def me(
    user: AuthenticatedUser = Depends(get_current_user),
    permission_reader: PermissionReader = Depends(get_permission_reader),
):
    # CA-09: permisos efectivos del rol vigente, leídos de la base en cada llamada.
    return SessionInfo(
        id_usuario=user.user_id,
        username=user.username,
        rol=user.role,
        permisos=sorted(permission_reader.permissions_for_role(user.role)),
    )
