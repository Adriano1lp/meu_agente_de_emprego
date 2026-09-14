from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from types import ModuleType
from unittest.mock import MagicMock

from config import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION
from database.repository import update_user_billing, update_user_consent
from services.billing import consume_processar_quota, current_usage_period


def _install_heavy_service_stubs() -> None:
    stubs = {
        "services.main_chat": {
            "generate_cover_letter": MagicMock(),
            "pipeline_with_details": MagicMock(
                return_value={
                    "resposta_usuario": "Analise ok",
                    "match_score": 80,
                    "should_generate_curriculum": False,
                    "vaga": {"cargo": "Dev"},
                    "matching": {"pontos_fortes": ["Python"]},
                    "otimizacao": {},
                }
            ),
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


def _register(client, email: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Usuario Status",
            "email": email,
            "password": "senha-forte-123",
            "terms_accepted": True,
            "terms_version": CURRENT_TERMS_VERSION,
            "privacy_accepted": True,
            "privacy_version": CURRENT_PRIVACY_VERSION,
        },
    )
    assert response.status_code == 200
    body = response.json()
    return {
        "user_id": body["user"]["user_id"],
        "auth": {"Authorization": f"Bearer {body['access_token']}"},
        "user": body["user"],
    }


def _activate_essencial(user_id: str) -> None:
    update_user_billing(
        user_id,
        plan="essencial",
        subscription_status="active",
        stripe_customer_id="cus_status_test",
        stripe_subscription_id="sub_status_test",
        updated_at="2026-09-01T00:00:00+00:00",
    )


def _set_processar_usage(db_path, user_id: str, period: str, used: int) -> None:
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO processar_usage (user_id, period, used, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, period) DO UPDATE SET
                used = excluded.used,
                updated_at = excluded.updated_at
            """,
            (user_id, period, used, now),
        )


def test_status_includes_quota_fields_for_free_plan(isolated_db):
    client = _client()
    session = _register(client, "status.free@example.com")

    response = client.get("/users/me/status", headers=session["auth"])
    assert response.status_code == 200
    body = response.json()
    assert "plan" in body
    assert "used" in body
    assert "limit" in body
    assert "remaining" in body
    assert body["plan"] == "free"
    assert body["used"] == 0
    assert body["limit"] == 5
    assert body["remaining"] == 5
    assert body["period"] == current_usage_period()
    assert body["subscription_status"] == "none"
    assert body["user_id"] == session["user_id"]
    assert "has_cv" in body
    assert "has_profile" in body
    assert "has_embeddings" in body
    assert "generated_files" in body
    assert "email" not in body
    assert "display_name" not in body
    assert "stored_plan" not in body


def test_status_free_limit_is_five(isolated_db):
    client = _client()
    session = _register(client, "status.free-limit@example.com")
    consume_processar_quota(session["user_id"])
    consume_processar_quota(session["user_id"])

    body = client.get("/users/me/status", headers=session["auth"]).json()
    assert body["plan"] == "free"
    assert body["limit"] == 5
    assert body["used"] == 2
    assert body["remaining"] == max(0, body["limit"] - body["used"])
    assert body["remaining"] == 3


def test_status_essencial_active_limit_is_thirty(isolated_db):
    client = _client()
    session = _register(client, "status.essencial@example.com")
    _activate_essencial(session["user_id"])

    body = client.get("/users/me/status", headers=session["auth"]).json()
    assert body["plan"] == "essencial"
    assert body["subscription_status"] == "active"
    assert body["limit"] == 30
    assert body["used"] == 0
    assert body["remaining"] == 30


def test_status_essencial_without_active_falls_back_to_free_limit(isolated_db):
    client = _client()
    session = _register(client, "status.essencial-past-due@example.com")
    update_user_billing(
        session["user_id"],
        plan="essencial",
        subscription_status="past_due",
        stripe_customer_id="cus_status_past_due",
        stripe_subscription_id="sub_status_past_due",
        updated_at="2026-09-01T00:00:00+00:00",
    )

    body = client.get("/users/me/status", headers=session["auth"]).json()
    assert body["plan"] == "free"
    assert body["subscription_status"] == "past_due"
    assert body["limit"] == 5
    assert body["remaining"] == max(0, body["limit"] - body["used"])


def test_status_remaining_never_negative(isolated_db):
    client = _client()
    session = _register(client, "status.over-quota@example.com")
    _set_processar_usage(
        isolated_db,
        session["user_id"],
        current_usage_period(),
        used=9,
    )

    body = client.get("/users/me/status", headers=session["auth"]).json()
    assert body["plan"] == "free"
    assert body["limit"] == 5
    assert body["used"] == 9
    assert body["remaining"] == max(0, body["limit"] - body["used"])
    assert body["remaining"] == 0


def test_status_terms_outdated_still_403(isolated_db):
    client = _client()
    session = _register(client, "status.terms-outdated@example.com")
    update_user_consent(
        session["user_id"],
        doc="terms",
        version="0.9",
        accepted_at="2020-01-01T00:00:00+00:00",
    )

    blocked = client.get("/users/me/status", headers=session["auth"])
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "TERMS_OUTDATED"


def test_status_privacy_outdated_still_403(isolated_db):
    client = _client()
    session = _register(client, "status.privacy-outdated@example.com")
    update_user_consent(
        session["user_id"],
        doc="privacy",
        version="0.9",
        accepted_at="2020-01-01T00:00:00+00:00",
    )

    blocked = client.get("/users/me/status", headers=session["auth"])
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "PRIVACY_OUTDATED"
