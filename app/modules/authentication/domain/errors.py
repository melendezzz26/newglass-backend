"""Errores del módulo de autenticación. No dependen de HTTP ni de la base."""


class InvalidUsernameError(ValueError):
    """El usuario ingresado no tiene el formato de una letra y 8 dígitos."""


class InvalidCredentialsError(Exception):
    """El usuario no existe, no tiene un rol válido o la contraseña no coincide."""


class InactiveAccountError(Exception):
    """Las credenciales son correctas, pero la cuenta está desactivada."""


class SessionNotValidError(Exception):
    """La cuenta de una sesión abierta ya no existe o fue desactivada."""


class TooManyLoginAttemptsError(Exception):
    """La cuenta o la IP superó el límite de intentos fallidos (EN-SEC-001).

    No indica cuál de las dos claves está bloqueada ni si la cuenta existe.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Demasiados intentos fallidos.")
        self.retry_after_seconds = retry_after_seconds
