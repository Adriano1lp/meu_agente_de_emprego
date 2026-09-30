from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime
from types import ModuleType

import pytest

DATA_D = "30 de setembro de 2026"
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


def test_formatar_data_carta_usa_calendario_de_sao_paulo(main_chat):
    # 01/10/2026 02:30 UTC ainda e 30/09/2026 em America/Sao_Paulo.
    momento_d = datetime(2026, 10, 1, 2, 30, tzinfo=UTC)
    assert main_chat.formatar_data_carta(momento_d) == DATA_D
    assert main_chat.formatar_data_carta(datetime(2026, 3, 1, 8, 0)) == "1 de março de 2026"


@pytest.mark.parametrize(
    "trecho",
    [
        "[data atual]",
        "[Data Atual]",
        "[ data atual ]",
        "data atual",
        "DATA ATUAL",
    ],
)
def test_aplicar_data_na_carta_remove_placeholder(main_chat, trecho: str):
    texto = f"Curitiba, {trecho}. A data atualizada permanece."
    resultado = main_chat.aplicar_data_na_carta(texto, DATA_D)

    assert main_chat._PLACEHOLDER_DATA_ATUAL.search(resultado) is None
    assert "[data atual]" not in resultado.lower()
    assert DATA_D in resultado
    assert "data atualizada" in resultado


def test_geracao_de_carta_monta_prompt_e_texto_com_data_real(main_chat, monkeypatch):
    """POST de carta na data D: prompt e texto usam D em pt-BR, sem placeholder."""
    monkeypatch.setattr(
        main_chat,
        "_agora_carta",
        lambda: datetime(2026, 10, 1, 2, 30, tzinfo=UTC),
    )
    monkeypatch.setattr(
        main_chat,
        "_load_candidate_context",
        lambda empresa, user_id: "Ana Silva, desenvolvedora Python",
    )
    capturado: dict[str, str] = {}

    class _CadeiaFalsa:
        def invoke(self, payload: dict[str, str]) -> str:
            capturado.update(payload)
            return (
                "Curitiba, [data atual]\n\n"
                "Prezados recrutadores da Acme,\n\n"
                "Tenho interesse. Variante [Data Atual] e data atual.\n"
                "A data atualizada do curriculo segue em anexo.\n\n"
                "Atenciosamente,\n"
                "Ana Silva"
            )

    monkeypatch.setattr(main_chat, "cadeia_carta_apresentacao", _CadeiaFalsa())

    carta = main_chat.generate_cover_letter("Acme", "user-1")
    prompt = main_chat.prompt_carta_apresentacao.format(**capturado)

    assert capturado["empresa"] == "Acme"
    assert capturado["data"] == DATA_D
    assert DATA_D in prompt
    assert "[Cidade]" in prompt
    assert "[data atual]" not in prompt.lower()
    assert "data atual" not in prompt.lower()
    assert "[data atual]" not in main_chat.prompt_carta_apresentacao.template.lower()
    assert carta.startswith("Curitiba, 30 de setembro de 2026")
    assert main_chat._PLACEHOLDER_DATA_ATUAL.search(carta) is None
    assert "[data atual]" not in carta.lower()
    assert "data atualizada" in carta
