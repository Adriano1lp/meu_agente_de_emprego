from __future__ import annotations

import importlib
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

_MODULO_REAL: ModuleType | None = None


@pytest.fixture
def main_chat():
    """Carrega o modulo real sem deixar o stub dos outros testes permanente."""
    global _MODULO_REAL
    import services

    anterior = sys.modules.get("services.main_chat")
    atributo_anterior = getattr(services, "main_chat", None)
    if _MODULO_REAL is None:
        sys.modules.pop("services.main_chat", None)
        _MODULO_REAL = importlib.import_module("services.main_chat")
    else:
        sys.modules["services.main_chat"] = _MODULO_REAL
        services.main_chat = _MODULO_REAL

    try:
        yield _MODULO_REAL
    finally:
        if anterior is None:
            sys.modules.pop("services.main_chat", None)
        else:
            sys.modules["services.main_chat"] = anterior
        if atributo_anterior is None:
            if hasattr(services, "main_chat"):
                delattr(services, "main_chat")
        else:
            services.main_chat = atributo_anterior


class _RetrieverVazio:
    def invoke(self, _texto: str) -> list[object]:
        return []


def test_pipeline_contexto_vazio_responde_400_antes_do_llm(main_chat, monkeypatch):
    monkeypatch.setattr(main_chat, "_use_mongodb_embeddings", lambda: False)
    monkeypatch.setattr(main_chat, "_build_user_retriever", lambda _user_id: _RetrieverVazio())
    monkeypatch.setattr(main_chat, "_read_cv_file", lambda _user_id: "")
    cadeia = MagicMock(side_effect=AssertionError("llm nao deveria rodar"))
    monkeypatch.setattr(main_chat, "cadeia_1", cadeia)

    with pytest.raises(HTTPException) as exc:
        main_chat.pipeline_with_details("Vaga para desenvolvedora Python", "user-1")

    assert exc.value.status_code == 400
    assert exc.value.detail == main_chat.EMPTY_CANDIDATE_CONTEXT_DETAIL
    assert "Nao foi possivel carregar o contexto do candidato" in exc.value.detail
    assert cadeia.invoke.call_count == 0


def test_pipeline_reusa_contexto_ja_carregado(main_chat, monkeypatch):
    monkeypatch.setattr(
        main_chat,
        "_load_candidate_context",
        MagicMock(side_effect=AssertionError("nao deveria recarregar o contexto")),
    )

    class _PareAntesDoLlm(Exception):
        pass

    cadeia = MagicMock()
    cadeia.invoke.side_effect = _PareAntesDoLlm()
    monkeypatch.setattr(main_chat, "cadeia_1", cadeia)

    with pytest.raises(_PareAntesDoLlm):
        main_chat.pipeline_with_details(
            "Vaga para desenvolvedora Python",
            "user-1",
            contexto="Ana Silva, desenvolvedora Python",
        )

    assert cadeia.invoke.call_count == 1
    assert main_chat._load_candidate_context.call_count == 0
