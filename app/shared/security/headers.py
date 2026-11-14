"""Middleware de cabeceras HTTP de seguridad (OWASP ASVS / Hardening defensivo)."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

SECURITY_HEADERS = {
    # Evita que el navegador intente adivinar el MIME-type (MIME-sniffing)
    "X-Content-Type-Options": "nosniff",
    # Protege contra Clickjacking impidiendo que la app sea embebida en iframes externos
    "X-Frame-Options": "DENY",
    # Controla la cantidad de información enviada en la cabecera Referer
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Restringe políticas de contenido cross-domain
    "X-Permitted-Cross-Domain-Policies": "none",
    # Content-Security-Policy restrictivo para API REST
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; object-src 'none';",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response
