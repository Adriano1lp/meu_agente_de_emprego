"""Limite em memoria de POST /auth/refresh, por IP e por familia.

O contador vive no processo. Nao e compartilhado entre instancias.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque

from starlette.requests import Request

import config

_JANELA_SEGUNDOS = 60
_TETO_CHAVES = 10_000
_lock = threading.Lock()
_baldes: OrderedDict[str, deque[float]] = OrderedDict()


def reset_refresh_rate_limits() -> None:
    with _lock:
        _baldes.clear()


def client_ip_for_refresh_limit(request: Request) -> str:
    """IP do limite. A ultima entrada do XFF e a que o proxy acrescenta.

    Nao usa a primeira entrada: o cliente pode forja-la. Sem XFF util, cai no peer.
    """
    hops = _hops(config.RATE_LIMIT_TRUST_PROXY_HOPS)
    if hops >= 1:
        partes = _entradas_xff(request)
        if len(partes) >= hops:
            return partes[-hops]
    return _peer_host(request)


def consume_refresh_rate_limit(*, client_ip: str, family_id: str | None) -> int | None:
    """Registra a tentativa. Devolve Retry-After em segundos se o limite estourou.

    A recusa nao entra no balde, para a espera nao crescer a cada 429.
    O limite por IP so conta quando a flag esta ligada. O de familia, sempre.
    """
    agora = time.monotonic()
    ip_ligado = config.REFRESH_RATE_LIMIT_PER_IP_ENABLED is True
    ip_limite = _limite(config.AUTH_REFRESH_RATE_LIMIT_PER_IP)
    familia_limite = _limite(config.AUTH_REFRESH_RATE_LIMIT_PER_FAMILY)
    chave_ip = f"ip:{client_ip}"
    chave_familia = f"familia:{family_id}" if family_id else None
    with _lock:
        espera_ip = _espera(chave_ip, ip_limite, agora) if ip_ligado else None
        espera_familia = (
            _espera(chave_familia, familia_limite, agora) if chave_familia else None
        )
        if espera_ip is not None or espera_familia is not None:
            return max(item for item in (espera_ip, espera_familia) if item is not None)
        if ip_ligado:
            _registrar(chave_ip, agora)
        if chave_familia:
            _registrar(chave_familia, agora)
        return None


def _entradas_xff(request: Request) -> list[str]:
    partes: list[str] = []
    for valor in request.headers.getlist("x-forwarded-for"):
        partes.extend(parte.strip() for parte in valor.split(",") if parte.strip())
    return partes


def _peer_host(request: Request) -> str:
    client = request.client
    if client is None or not client.host:
        return "desconhecido"
    return client.host


def _hops(valor: object) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        return config._TRUST_PROXY_HOPS_PADRAO
    return valor


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
    _baldes.move_to_end(chave)
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
        while len(_baldes) >= _TETO_CHAVES:
            _baldes.popitem(last=False)
        balde = deque()
        _baldes[chave] = balde
    else:
        _baldes.move_to_end(chave)
    balde.append(agora)
