from __future__ import annotations

import hashlib
import hmac
import json
import time

from services.billing import consume_llm_quota, current_usage_period


def _register(client, email: str) -> dict[str, str]:
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Usuario Billing",
            "email": email,
            "password": "senha-forte-123",
            "terms_accepted": True,
            "privacy_accepted": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    return {
        "user_id": body["user"]["user_id"],
        "auth": {"Authorization": f"Bearer {body['access_token']}"},
    }


def _sign_stripe_payload(payload: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hmac.new(
        b"whsec_test",
        f"{timestamp}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    return {"Stripe-Signature": f"t={timestamp},v1={digest}"}


def test_billing_me_defaults_to_free(client) -> None:
    session = _register(client, "billing.free@example.com")
    response = client.get("/billing/me", headers=session["auth"])
    assert response.status_code == 200
    body = response.json()
    assert body["plan"] == "free"
    assert body["quota"] == 2
    assert body["used"] == 0
    assert body["remaining"] == 2


def test_checkout_without_stripe_config_returns_503(client) -> None:
    session = _register(client, "billing.checkout@example.com")
    response = client.post("/billing/checkout", headers=session["auth"])
    assert response.status_code == 503


def test_webhook_rejects_invalid_signature(client) -> None:
    payload = json.dumps({"type": "checkout.session.completed", "data": {"object": {}}}).encode()
    response = client.post(
        "/billing/webhook",
        content=payload,
        headers={"Stripe-Signature": "t=1,v1=invalid", "Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_webhook_activates_essencial_subscription(client) -> None:
    session = _register(client, "billing.paid@example.com")
    payload = json.dumps(
        {
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
    response = client.post(
        "/billing/webhook",
        content=payload,
        headers={**_sign_stripe_payload(payload), "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    entitlement = client.get("/billing/me", headers=session["auth"]).json()
    assert entitlement["plan"] == "essencial"
    assert entitlement["quota"] == 10
    assert entitlement["stripe_subscription_id"] == "sub_test_123"


def test_processar_is_blocked_when_free_quota_exhausted(client) -> None:
    session = _register(client, "billing.quota@example.com")
    consume_llm_quota(session["user_id"], "processar")
    consume_llm_quota(session["user_id"], "processar")
    assert current_usage_period()

    response = client.post(
        "/processar",
        headers=session["auth"],
        json={"texto": "Descricao de vaga para teste de cota"},
    )
    assert response.status_code == 402
    detail = response.json()["detail"]
    assert detail["code"] == "quota_exceeded"
    assert detail["plan"] == "free"
    assert detail["used"] == 2
    assert detail["quota"] == 2
