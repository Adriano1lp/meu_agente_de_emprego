from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

from config import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION
from database.repository import list_consent_log, update_user_consent


def _install_heavy_service_stubs() -> None:
    stubs = {
        "services.main_chat": {
            "generate_cover_letter": MagicMock(),
            "pipeline_with_details": MagicMock(),
        },
        "services.main_carta": {
            "gerar_pdf_carta_apresentacao": MagicMock(),
        },
        "services.main_curriculo": {
            "gerar_pdf_profissional": MagicMock(),
        },
        "services.main_rag": {
            "rebuild_vectorstore_for_user": MagicMock(),
        },
        "services.development_plan": {
            "DEFAULT_ANALYSIS_LIMIT": 10,
            "MAX_ANALYSIS_LIMIT": 20,
            "generate_development_plan": MagicMock(),
            "read_active_development_plan": MagicMock(),
            "read_development_plan_history": MagicMock(),
            "update_development_plan_item_status": MagicMock(),
        },
        "services.user_data": {
            "get_user_profile": MagicMock(),
            "save_manual_profile": MagicMock(),
            "save_user_cv": MagicMock(),
            "save_user_profile": MagicMock(),
        },
    }
    for name, attributes in stubs.items():
        if name in sys.modules:
            continue
        module = ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[name] = module


def _client():
    _install_heavy_service_stubs()
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)


def test_legal_endpoints_serve_v1_and_404_unknown(isolated_db):
    client = _client()
    terms = client.get("/legal/terms", params={"version": "1.0"})
    privacy = client.get("/legal/privacy", params={"version": "1.0"})
    assert terms.status_code == 200
    assert privacy.status_code == 200
    assert "Termos de Uso" in terms.text
    assert "Politica de Privacidade" in privacy.text
    assert client.get("/legal/terms", params={"version": "9.9"}).status_code == 404
    assert client.get("/legal/privacy", params={"version": "9.9"}).status_code == 404


def test_register_and_consent_http_flow(isolated_db):
    client = _client()
    missing = client.post(
        "/auth/register",
        json={
            "display_name": "Ada Lovelace",
            "email": "ada-http@example.com",
            "password": "senha-forte-123",
        },
    )
    assert missing.status_code == 400

    created = client.post(
        "/auth/register",
        json={
            "display_name": "Ada Lovelace",
            "email": "ada-http@example.com",
            "password": "senha-forte-123",
            "terms_accepted": True,
            "terms_version": CURRENT_TERMS_VERSION,
            "privacy_accepted": True,
            "privacy_version": CURRENT_PRIVACY_VERSION,
        },
    )
    assert created.status_code == 200
    token = created.json()["access_token"]
    user_id = created.json()["user"]["user_id"]
    assert len(list_consent_log(user_id)) == 2

    headers = {"Authorization": f"Bearer {token}"}
    update_user_consent(
        user_id,
        doc="terms",
        version="0.9",
        accepted_at="2020-01-01T00:00:00+00:00",
    )
    blocked = client.get("/users/me", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "TERMS_OUTDATED"

    legal_still_public = client.get("/legal/terms", params={"version": "1.0"})
    assert legal_still_public.status_code == 200

    reaccept = client.post(
        "/consent",
        headers=headers,
        json={"doc": "terms", "version": CURRENT_TERMS_VERSION},
    )
    assert reaccept.status_code == 200
    assert reaccept.json()["terms_version"] == CURRENT_TERMS_VERSION
    assert client.get("/users/me", headers=headers).status_code == 200
    assert len(list_consent_log(user_id)) == 3
