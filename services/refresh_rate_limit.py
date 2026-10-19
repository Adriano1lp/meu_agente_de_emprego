"""Limite em memoria de POST /auth/refresh, por IP e por familia."""

from __future__ import annotations

import math
import threading
import time
from collections import deque

import config

_JANELA_SEGUNDOS = 60
_lock = threading.Lock()
_baldes: dict[str, deque[float]] = {}


def reset_refresh_rate_limits() -> None:
    with _lock:
        _baldes.clear()


def consume_refresh_rate_limit(*, client_ip: str, family_id: str | None) -> int | None:
    """Registra a tentativa. Devolve Retry-After em segundos se o limite estourou.

    A recusa nao entra no balde, para a espera nao crescer a cada 429.
    """
    agora = time.monotonic()
    ip_limite = _limite(config.AUTH_REFRESH_RATE_LIMIT_PER_IP)
    familia_limite = _limite(config.AUTH_REFRESH_RATE_LIMIT_PER_FAMILY)
    chave_ip = f"ip:{client_ip}"
    chave_familia = f"familia:{family_id}" if family_id else None
    with _lock:
        espera_ip = _espera(chave_ip, ip_limite, agora)
        espera_familia = (
            _espera(chave_familia, familia_limite, agora) if chave_familia else None
        )
        if espera_ip is not None or espera_familia is not None:
            return max(item for item in (espera_ip, espera_familia) if item is not None)
        _registrar(chave_ip, agora)
        if chave_familia:
            _registrar(chave_familia, agora)
        return None


def _limite(valor: object) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        return config._REFRESH_RATE_LIMIT_PADRAO
    return valor


def _podar(chave: str, agora: float) -> deque[float]:
    balde = _baldes.get(chave)
    if balde is None:
        return deque()
    corte = agora - _JANELA_SEGUNDOS
    while balde and balde[0] <= corte:
        balde.popleft()
    if not balde:
        _baldes.pop(chave, None)
    return balde


def _espera(chave: str, limite: int, agora: float) -> int | None:
    balde = _podar(chave, agora)
    if len(balde) < limite:
        return None
    if not balde:
        return 1
    restante = balde[0] + _JANELA_SEGUNDOS - agora
    return max(1, math.ceil(restante))


def _registrar(chave: str, agora: float) -> None:
    balde = _baldes.get(chave)
    if balde is None:
        balde = deque()
        _baldes[chave] = balde
    balde.append(agora)
