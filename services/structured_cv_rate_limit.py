"""Cota de POST e PUT /users/me/cv/structured, por usuario.

A janela e de uma hora e o padrao e 10 gravacoes validas
(STRUCTURED_CV_RATE_LIMIT_PER_HOUR). Cada reserva e uma linha no SQLite
ou um item no documento do usuario no Mongo, com validade de 1 hora.
O contador sobrevive a reinicio e vale para todas as instancias.

O servico devolve um token na reserva. O estorno apaga esse token, uma
vez. Pedido invalido nao entra na cota: a reserva so acontece depois da
validacao, e some se a gravacao nao persiste.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import config

_JANELA_SEGUNDOS = 60 * 60
_PADRAO = 10


def reset_structured_cv_rate_limits() -> None:
    """Nao apaga a cota no banco. O processo novo enxerga as mesmas reservas."""
    return None


def limite_por_hora() -> int:
    valor = config.structured_cv_rate_limit_per_hour()
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 1:
        return _PADRAO
    return valor


def consume_structured_cv_write(user_id: str) -> str | int:
    """Reserva uma gravacao. Devolve o token, ou Retry-After em segundos.

    A recusa nao grava reserva, para a espera nao crescer a cada 429.
    """
    from database.repository import reservar_structured_cv_write

    agora = _agora()
    resultado = reservar_structured_cv_write(
        user_id,
        limite=limite_por_hora(),
        janela_segundos=_JANELA_SEGUNDOS,
        agora=agora,
    )
    token = resultado.get("token")
    if isinstance(token, str) and token:
        return token
    expira = resultado.get("expires_at")
    if isinstance(expira, datetime):
        return _espera(expira, agora)
    return 1


def release_structured_cv_write(user_id: str, token: str) -> None:
    """Devolve a reserva identificada pelo token. Nao mexe nas outras."""
    if not user_id or not token:
        return
    from database.repository import liberar_structured_cv_write

    liberar_structured_cv_write(user_id, token, agora=_agora())


def _agora() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _espera(expira: datetime, agora: datetime) -> int:
    if expira.tzinfo is None:
        expira = expira.replace(tzinfo=UTC)
    else:
        expira = expira.astimezone(UTC)
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=UTC)
    else:
        agora = agora.astimezone(UTC)
    restante = (expira - agora).total_seconds()
    if restante <= 0:
        return 1
    return max(1, math.ceil(restante))
