"""Middleware de cabeceras HTTP de seguridad (OWASP ASVS / Hardening defensivo).

Implementado como middleware ASGI puro para máxima eficiencia, cero sobrecarga
de memoria y preservación estricta de desconexiones de cliente y streaming.
"""
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURITY_HEADERS = [
    # Evita que el navegador intente adivinar el MIME-type (MIME-sniffing)
    ("x-content-type-options", "nosniff"),
    # Protege contra Clickjacking impidiendo que la app sea embebida en iframes externos
    ("x-frame-options", "DENY"),
    # Controla la cantidad de información enviada en la cabecera Referer
    ("referrer-policy", "strict-origin-when-cross-origin"),
    # Restringe políticas de contenido cross-domain
    ("x-permitted-cross-domain-policies", "none"),
    # Content-Security-Policy restrictivo para API REST
    ("content-security-policy", "default-src 'self'; frame-ancestors 'none'; object-src 'none';"),
]


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_wrapper)
