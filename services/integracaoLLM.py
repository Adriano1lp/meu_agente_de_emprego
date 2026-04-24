from __future__ import annotations

from services.main_chat import pipeline


def analisar_vaga(vaga: str) -> str:
    if not vaga or not vaga.strip():
        raise ValueError("A vaga nao pode estar vazia.")

    _, resposta_usuario = pipeline(vaga)
    return resposta_usuario
