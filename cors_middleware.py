from __future__ import annotations

from starlette.middleware.cors import CORSMiddleware

import config

# Metodos e headers explicitos. O dominio da web vem de CORS_ALLOW_ORIGINS.
CORS_ALLOW_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
CORS_ALLOW_HEADERS = ("Authorization", "Content-Type", "X-MAE-CSRF")


class DynamicCORSMiddleware(CORSMiddleware):
    """CORS com credenciais, lendo a allowlist a cada request.

    Testes trocam CORS_ALLOW_ORIGINS depois do import. Produção continua
    recusando '*' no boot. Com credenciais, a origem permitida é ecoada
    e a resposta leva Vary: Origin.
    """

    def __init__(self, app):
        configured = _configured_origins()
        super().__init__(
            app,
            allow_origins=configured,
            allow_credentials=True,
            allow_methods=CORS_ALLOW_METHODS,
            allow_headers=CORS_ALLOW_HEADERS,
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            self._apply_configured_origins()
        await super().__call__(scope, receive, send)

    def _apply_configured_origins(self) -> None:
        configured = _configured_origins()
        self.allow_origins = configured
        self.allow_all_origins = "*" in configured
        self.preflight_explicit_allow_origin = (
            not self.allow_all_origins or self.allow_credentials
        )
        simple_headers: dict[str, str] = {}
        if self.allow_all_origins and not self.allow_credentials:
            simple_headers["Access-Control-Allow-Origin"] = "*"
        if self.allow_credentials:
            simple_headers["Access-Control-Allow-Credentials"] = "true"
        self.simple_headers = simple_headers


def _configured_origins() -> list[str]:
    origins = [origin.strip() for origin in config.CORS_ALLOW_ORIGINS if origin.strip()]
    return origins or ["*"]
