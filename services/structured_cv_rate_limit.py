"""Limite em memoria de POST e PUT /users/me/cv/structured, por usuario.

O contador vive no processo, nao e compartilhado entre instancias e assume
um unico worker. Antes de escalar, migre para o banco ou para o Redis.
A janela e de uma hora. O padrao e 10 gravacoes; o valor vem do ambiente.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque

import config

_JANELA_SEGUNDOS = 60 * 60
_PADRAO = 10
_TETO_CHAVES = 10_000
_lock = threading.Lock()
_baldes: OrderedDict[str, deque[float]] = OrderedDict()


def reset_structured_cv_rate_limits() -> None:
    with _lock:
        _baldes.clear()


def limite_por_hora() -> int:
    valor = config.structured_cv_rate_limit_per_hour()
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 1:
        return _PADRAO
    return valor


def consume_structured_cv_write(user_id: str) -> int | None:
    """Registra a gravacao. Devolve Retry-After em segundos se o limite estourou.

    A recusa nao entra no balde, para a espera nao crescer a cada 429.
    """
    agora = time.monotonic()
    limite = limite_por_hora()
    chave = f"usuario:{user_id}"
    with _lock:
        espera = _espera(chave, limite, agora)
        if espera is not None:
            return espera
        _registrar(chave, agora)
        return None


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
