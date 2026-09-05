from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from config import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION
from database.repository import (
    get_processar_usage,
    get_user_by_id,
    update_user_billing,
)
from services.billing import (
    consume_processar_quota,
    current_usage_period,
    get_entitlement,
    handle_stripe_webhook,
)


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
            "display_name": "Usuario Billing",
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


def _sign_stripe_payload(payload: bytes, secret: str = "whsec_test") -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    return {"Stripe-Signature": f"t={timestamp},v1={digest}"}


def _activate_essencial(user_id: str) -> None:
    update_user_billing(
        user_id,
        plan="essencial",
        subscription_status="active",
        stripe_customer_id="cus_test",
        stripe_subscription_id="sub_test",
        updated_at="2026-09-01T00:00:00+00:00",
    )


def test_new_user_defaults_to_free_quota(isolated_db):
    client = _client()
    session = _register(client, "billing.free@example.com")
    assert session["user"]["plan"] == "free"
    assert session["user"]["subscription_status"] == "none"

    entitlement = client.get("/billing/me", headers=session["auth"]).json()
    assert entitlement["plan"] == "free"
    assert entitlement["limit"] == 5
    assert entitlement["used"] == 0
    assert entitlement["remaining"] == 5
    assert entitlement["period"] == current_usage_period()


def test_free_quota_allows_five_and_blocks_sixth(isolated_db):
    client = _client()
    session = _register(client, "billing.free-quota@example.com")
    user_id = session["user_id"]

    for _ in range(5):
        consume_processar_quota(user_id)

    with pytest.raises(HTTPException) as exc:
        consume_processar_quota(user_id)
    assert exc.value.status_code == 402
    detail = exc.value.detail
    assert detail["code"] == "SUBSCRIPTION_REQUIRED"
    assert detail["used"] == 5
    assert detail["limit"] == 5
    assert detail["plan"] == "free"
    assert get_processar_usage(user_id, current_usage_period()) == 5


def test_essencial_quota_allows_thirty_and_blocks_31st(isolated_db):
    client = _client()
    session = _register(client, "billing.essencial-quota@example.com")
    user_id = session["user_id"]
    _activate_essencial(user_id)

    for _ in range(30):
        consume_processar_quota(user_id)

    with pytest.raises(HTTPException) as exc:
        consume_processar_quota(user_id)
    assert exc.value.status_code == 402
    detail = exc.value.detail
    assert detail["code"] == "QUOTA_EXCEEDED"
    assert detail["used"] == 30
    assert detail["limit"] == 30
    assert detail["plan"] == "essencial"


def test_past_due_falls_back_to_free_quota(isolated_db):
    client = _client()
    session = _register(client, "billing.pastdue@example.com")
    user_id = session["user_id"]
    update_user_billing(
        user_id,
        plan="essencial",
        subscription_status="past_due",
        updated_at="2026-09-01T00:00:00+00:00",
    )
    entitlement = get_entitlement(user_id)
    assert entitlement["plan"] == "free"
    assert entitlement["limit"] == 5
    assert entitlement["subscription_status"] == "past_due"


def test_processar_returns_402_when_free_quota_exhausted(isolated_db):
    client = _client()
    session = _register(client, "billing.processar-402@example.com")
    for _ in range(5):
        consume_processar_quota(session["user_id"])

    response = client.post(
        "/processar",
        headers=session["auth"],
        json={"texto": "Vaga para desenvolvedor Python"},
    )
    assert response.status_code == 402
    detail = response.json()["detail"]
    assert detail["code"] == "SUBSCRIPTION_REQUIRED"
    assert detail["used"] == 5
    assert detail["limit"] == 5
    assert detail["plan"] == "free"
    assert "message" in detail


def test_processar_empty_text_does_not_consume_quota(isolated_db):
    client = _client()
    session = _register(client, "billing.empty@example.com")
    response = client.post(
        "/processar",
        headers=session["auth"],
        json={"texto": "   "},
    )
    assert response.status_code == 400
    assert get_processar_usage(session["user_id"], current_usage_period()) == 0


def test_processar_consumes_one_unit_when_allowed(isolated_db):
    client = _client()
    session = _register(client, "billing.processar-ok@example.com")
    response = client.post(
        "/processar",
        headers=session["auth"],
        json={"texto": "Vaga para analista de dados"},
    )
    assert response.status_code == 200
    assert get_processar_usage(session["user_id"], current_usage_period()) == 1


def test_checkout_requires_auth_and_consent(isolated_db):
    client = _client()
    assert client.post("/billing/checkout").status_code == 401

    session = _register(client, "billing.checkout-consent@example.com")
    update_user_billing(
        session["user_id"],
        updated_at="2020-01-01T00:00:00+00:00",
    )
    from database.repository import update_user_consent

    update_user_consent(
        session["user_id"],
        doc="terms",
        version="0.9",
        accepted_at="2020-01-01T00:00:00+00:00",
    )
    blocked = client.post("/billing/checkout", headers=session["auth"])
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "TERMS_OUTDATED"


def test_checkout_without_stripe_config_returns_503(isolated_db):
    client = _client()
    session = _register(client, "billing.checkout-503@example.com")
    response = client.post("/billing/checkout", headers=session["auth"])
    assert response.status_code == 503


def test_checkout_creates_subscription_session(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "billing.checkout-ok@example.com")
    monkeypatch.setattr("config.STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setattr("config.STRIPE_PRICE_ESSENCIAL", "price_essencial_test")
    monkeypatch.setattr(
        "config.STRIPE_CHECKOUT_SUCCESS_URL",
        "https://app.example/ok",
    )
    monkeypatch.setattr(
        "config.STRIPE_CHECKOUT_CANCEL_URL",
        "https://app.example/cancel",
    )

    captured: dict = {}

    class FakeSession:
        id = "cs_test_123"
        url = "https://checkout.stripe.com/c/pay/cs_test_123"

    def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeSession()

    monkeypatch.setattr(
        "services.billing._create_stripe_checkout_session",
        fake_create,
    )
    response = client.post("/billing/checkout", headers=session["auth"])
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "cs_test_123"
    assert body["checkout_url"].startswith("https://checkout.stripe.com/")
    assert captured["mode"] == "subscription"
    assert captured["line_items"] == [{"price": "price_essencial_test", "quantity": 1}]
    assert captured["client_reference_id"] == session["user_id"]


def test_webhook_rejects_invalid_signature(isolated_db, monkeypatch):
    monkeypatch.setattr("config.STRIPE_WEBHOOK_SECRET", "whsec_test")
    client = _client()
    payload = json.dumps({"id": "evt_bad", "type": "checkout.session.completed"}).encode()
    response = client.post(
        "/billing/webhook",
        content=payload,
        headers={"Stripe-Signature": "t=1,v1=invalid", "Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_webhook_without_secret_returns_503(isolated_db, monkeypatch):
    monkeypatch.setattr("config.STRIPE_WEBHOOK_SECRET", "")
    client = _client()
    payload = b"{}"
    response = client.post(
        "/billing/webhook",
        content=payload,
        headers=_sign_stripe_payload(payload),
    )
    assert response.status_code == 503


def test_webhook_activates_essencial_and_is_idempotent(isolated_db, monkeypatch):
    monkeypatch.setattr("config.STRIPE_WEBHOOK_SECRET", "whsec_test")
    client = _client()
    session = _register(client, "billing.webhook@example.com")
    payload = json.dumps(
        {
            "id": "evt_checkout_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": session["user_id"],
                    "customer": "cus_test_123",
                    "subscription": "sub_test_123",
                    "metadata": {"user_id": session["user_id"], "plan": "essencial"},
                }
            },
        }
    ).encode()
    headers = {
        **_sign_stripe_payload(payload),
        "Content-Type": "application/json",
    }
    first = client.post("/billing/webhook", content=payload, headers=headers)
    assert first.status_code == 200
    assert first.json().get("duplicate") is not True

    user = get_user_by_id(session["user_id"])
    assert user["plan"] == "essencial"
    assert user["subscription_status"] == "active"
    assert user["stripe_customer_id"] == "cus_test_123"
    assert user["stripe_subscription_id"] == "sub_test_123"

    second = client.post("/billing/webhook", content=payload, headers=headers)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    again = get_user_by_id(session["user_id"])
    assert again["plan"] == "essencial"
    assert again["stripe_subscription_id"] == "sub_test_123"

    entitlement = client.get("/billing/me", headers=session["auth"]).json()
    assert entitlement["plan"] == "essencial"
    assert entitlement["limit"] == 30


def test_webhook_cancel_returns_to_free_quota(isolated_db, monkeypatch):
    monkeypatch.setattr("config.STRIPE_WEBHOOK_SECRET", "whsec_test")
    client = _client()
    session = _register(client, "billing.cancel@example.com")
    _activate_essencial(session["user_id"])
    payload = json.dumps(
        {
            "id": "evt_sub_deleted_1",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "id": "sub_test",
                    "customer": "cus_test",
                    "metadata": {"user_id": session["user_id"]},
                }
            },
        }
    ).encode()
    response = client.post(
        "/billing/webhook",
        content=payload,
        headers={**_sign_stripe_payload(payload), "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    user = get_user_by_id(session["user_id"])
    assert user["plan"] == "free"
    assert user["subscription_status"] == "canceled"
    entitlement = get_entitlement(session["user_id"])
    assert entitlement["plan"] == "free"
    assert entitlement["limit"] == 5


def test_invoice_payment_failed_marks_past_due(isolated_db, monkeypatch):
    monkeypatch.setattr("config.STRIPE_WEBHOOK_SECRET", "whsec_test")
    client = _client()
    session = _register(client, "billing.invoice-fail@example.com")
    _activate_essencial(session["user_id"])
    payload = json.dumps(
        {
            "id": "evt_invoice_fail_1",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "customer": "cus_test",
                    "subscription": "sub_test",
                }
            },
        }
    ).encode()
    response = handle_stripe_webhook(payload, _sign_stripe_payload(payload)["Stripe-Signature"])
    assert response["received"] is True
    user = get_user_by_id(session["user_id"])
    assert user["subscription_status"] == "past_due"
    assert get_entitlement(session["user_id"])["limit"] == 5


def test_usage_resets_on_new_utc_month(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "billing.reset@example.com")
    monkeypatch.setattr("services.billing.current_usage_period", lambda: "2026-08")
    for _ in range(5):
        consume_processar_quota(session["user_id"])
    assert get_processar_usage(session["user_id"], "2026-08") == 5

    monkeypatch.setattr("services.billing.current_usage_period", lambda: "2026-09")
    consume_processar_quota(session["user_id"])
    assert get_processar_usage(session["user_id"], "2026-09") == 1
    assert get_entitlement(session["user_id"])["used"] == 1
