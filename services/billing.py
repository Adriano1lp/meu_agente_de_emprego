from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

import config as app_config
from config import sanitize_user_id
from database.repository import (
    claim_stripe_webhook_event,
    consume_processar_usage,
    get_processar_usage,
    get_user_by_id,
    get_user_by_stripe_customer_id,
    get_user_by_stripe_subscription_id,
    update_user_billing,
)

PLAN_FREE = "free"
PLAN_ESSENCIAL = "essencial"
SUBSCRIPTION_ACTIVE = "active"
SUBSCRIPTION_PAST_DUE = "past_due"
SUBSCRIPTION_CANCELED = "canceled"
SUBSCRIPTION_NONE = "none"


def current_usage_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def monthly_quota_for_plan(plan: str) -> int:
    if plan == PLAN_ESSENCIAL:
        return app_config.ESSENCIAL_PROCESSAR_QUOTA_MONTHLY
    return app_config.FREE_PROCESSAR_QUOTA_MONTHLY


def get_entitlement(user_id: str) -> dict[str, Any]:
    safe_user_id = sanitize_user_id(user_id)
    user = get_user_by_id(safe_user_id) or {}
    stored_plan = str(user.get("plan") or PLAN_FREE)
    subscription_status = str(user.get("subscription_status") or SUBSCRIPTION_NONE)
    effective_plan = (
        PLAN_ESSENCIAL
        if stored_plan == PLAN_ESSENCIAL and subscription_status == SUBSCRIPTION_ACTIVE
        else PLAN_FREE
    )
    period = current_usage_period()
    used = get_processar_usage(safe_user_id, period)
    limit = monthly_quota_for_plan(effective_plan)
    return {
        "user_id": safe_user_id,
        "plan": effective_plan,
        "stored_plan": stored_plan,
        "subscription_status": subscription_status,
        "period": period,
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used),
    }


def consume_processar_quota(user_id: str) -> dict[str, Any]:
    entitlement = get_entitlement(user_id)
    result = consume_processar_usage(
        entitlement["user_id"],
        period=entitlement["period"],
        limit=entitlement["limit"],
    )
    if not result["allowed"]:
        raise HTTPException(
            status_code=402,
            detail=_quota_denied_body(
                entitlement=entitlement,
                used=int(result["used"]),
            ),
        )

    entitlement["used"] = int(result["used"])
    entitlement["remaining"] = max(0, entitlement["limit"] - entitlement["used"])
    return entitlement


def create_checkout_session(user_id: str) -> dict[str, str]:
    secret_key = _stripe_secret_key()
    price_id = _stripe_price_essencial()
    if not secret_key or not price_id:
        raise HTTPException(status_code=503, detail="Billing Stripe nao configurado")

    safe_user_id = sanitize_user_id(user_id)
    user = get_user_by_id(safe_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")

    success_url = _checkout_success_url()
    cancel_url = _checkout_cancel_url()
    session_kwargs: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": safe_user_id,
        "metadata": {"user_id": safe_user_id, "plan": PLAN_ESSENCIAL},
        "subscription_data": {
            "metadata": {"user_id": safe_user_id, "plan": PLAN_ESSENCIAL},
        },
    }
    customer_id = user.get("stripe_customer_id")
    if isinstance(customer_id, str) and customer_id.strip():
        session_kwargs["customer"] = customer_id.strip()
    else:
        session_kwargs["customer_email"] = user["email"]

    session = _create_stripe_checkout_session(**session_kwargs)
    checkout_url = getattr(session, "url", None) or (
        session.get("url") if isinstance(session, dict) else None
    )
    session_id = getattr(session, "id", None) or (
        session.get("id") if isinstance(session, dict) else None
    )
    if not checkout_url or not session_id:
        raise HTTPException(status_code=502, detail="Stripe nao retornou checkout session")
    return {
        "checkout_url": str(checkout_url),
        "session_id": str(session_id),
    }


def handle_stripe_webhook(payload: bytes, signature_header: str | None) -> dict[str, Any]:
    secret = _stripe_webhook_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="STRIPE_WEBHOOK_SECRET nao configurado")
    if not signature_header:
        raise HTTPException(status_code=400, detail="Assinatura Stripe ausente")

    _verify_stripe_signature(payload, signature_header, secret)
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Payload Stripe invalido") from exc

    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    if event_id and not claim_stripe_webhook_event(event_id, event_type):
        return {"received": True, "duplicate": True, "type": event_type}

    data_object = _as_dict((event.get("data") or {}).get("object"))
    if event_type == "checkout.session.completed":
        _apply_checkout_session(data_object)
    elif event_type == "customer.subscription.updated":
        _apply_subscription_object(data_object)
    elif event_type == "customer.subscription.deleted":
        _apply_subscription_deleted(data_object)
    elif event_type == "invoice.paid":
        _apply_invoice_paid(data_object)
    elif event_type == "invoice.payment_failed":
        _apply_invoice_payment_failed(data_object)

    return {"received": True, "type": event_type}


def _quota_denied_body(*, entitlement: dict[str, Any], used: int) -> dict[str, Any]:
    if entitlement["plan"] == PLAN_ESSENCIAL:
        code = "QUOTA_EXCEEDED"
        message = "Cota mensal do plano Essencial esgotada."
    else:
        code = "SUBSCRIPTION_REQUIRED"
        message = (
            "Cota gratuita do mes esgotada. "
            "Assine o plano Essencial para continuar."
        )
    return {
        "code": code,
        "message": message,
        "used": used,
        "limit": entitlement["limit"],
        "plan": entitlement["plan"],
    }


def _apply_checkout_session(session: dict[str, Any]) -> None:
    user = _resolve_user(
        user_id=str(
            (session.get("metadata") or {}).get("user_id")
            or session.get("client_reference_id")
            or ""
        ),
        customer_id=_as_id(session.get("customer")),
        subscription_id=_as_id(session.get("subscription")),
    )
    if not user:
        return
    _write_billing(
        user_id=user["user_id"],
        stripe_customer_id=_as_id(session.get("customer")),
        stripe_subscription_id=_as_id(session.get("subscription")),
        plan=PLAN_ESSENCIAL,
        subscription_status=SUBSCRIPTION_ACTIVE,
    )


def _apply_subscription_object(subscription: dict[str, Any]) -> None:
    user = _resolve_user(
        user_id=str((subscription.get("metadata") or {}).get("user_id") or ""),
        customer_id=_as_id(subscription.get("customer")),
        subscription_id=_as_id(subscription.get("id")),
    )
    if not user:
        return

    plan, status = _map_subscription_status(str(subscription.get("status") or ""))
    _write_billing(
        user_id=user["user_id"],
        stripe_customer_id=_as_id(subscription.get("customer")),
        stripe_subscription_id=_as_id(subscription.get("id")),
        plan=plan,
        subscription_status=status,
        clear_subscription_id=status == SUBSCRIPTION_CANCELED and plan == PLAN_FREE,
    )


def _apply_subscription_deleted(subscription: dict[str, Any]) -> None:
    user = _resolve_user(
        user_id=str((subscription.get("metadata") or {}).get("user_id") or ""),
        customer_id=_as_id(subscription.get("customer")),
        subscription_id=_as_id(subscription.get("id")),
    )
    if not user:
        return
    _write_billing(
        user_id=user["user_id"],
        stripe_customer_id=_as_id(subscription.get("customer")),
        stripe_subscription_id=None,
        plan=PLAN_FREE,
        subscription_status=SUBSCRIPTION_CANCELED,
        clear_subscription_id=True,
    )


def _apply_invoice_paid(invoice: dict[str, Any]) -> None:
    user = _resolve_user(
        user_id=str((invoice.get("metadata") or {}).get("user_id") or ""),
        customer_id=_as_id(invoice.get("customer")),
        subscription_id=_as_id(invoice.get("subscription")),
    )
    if not user:
        return
    _write_billing(
        user_id=user["user_id"],
        stripe_customer_id=_as_id(invoice.get("customer")),
        stripe_subscription_id=_as_id(invoice.get("subscription")),
        plan=PLAN_ESSENCIAL,
        subscription_status=SUBSCRIPTION_ACTIVE,
    )


def _apply_invoice_payment_failed(invoice: dict[str, Any]) -> None:
    user = _resolve_user(
        user_id=str((invoice.get("metadata") or {}).get("user_id") or ""),
        customer_id=_as_id(invoice.get("customer")),
        subscription_id=_as_id(invoice.get("subscription")),
    )
    if not user:
        return
    _write_billing(
        user_id=user["user_id"],
        stripe_customer_id=_as_id(invoice.get("customer")),
        stripe_subscription_id=_as_id(invoice.get("subscription")),
        plan=PLAN_ESSENCIAL,
        subscription_status=SUBSCRIPTION_PAST_DUE,
    )


def _map_subscription_status(stripe_status: str) -> tuple[str, str]:
    status = stripe_status.strip().lower()
    if status == SUBSCRIPTION_ACTIVE:
        return PLAN_ESSENCIAL, SUBSCRIPTION_ACTIVE
    if status in {SUBSCRIPTION_PAST_DUE, "unpaid"}:
        return PLAN_ESSENCIAL, SUBSCRIPTION_PAST_DUE
    if status in {SUBSCRIPTION_CANCELED, "incomplete_expired"}:
        return PLAN_FREE, SUBSCRIPTION_CANCELED
    return PLAN_FREE, SUBSCRIPTION_NONE


def _resolve_user(
    *,
    user_id: str,
    customer_id: str | None,
    subscription_id: str | None,
) -> dict[str, Any] | None:
    if user_id.strip():
        try:
            user = get_user_by_id(sanitize_user_id(user_id))
        except ValueError:
            user = None
        if user:
            return user
    if subscription_id:
        user = get_user_by_stripe_subscription_id(subscription_id)
        if user:
            return user
    if customer_id:
        return get_user_by_stripe_customer_id(customer_id)
    return None


def _write_billing(
    *,
    user_id: str,
    stripe_customer_id: str | None,
    stripe_subscription_id: str | None,
    plan: str,
    subscription_status: str,
    clear_subscription_id: bool = False,
) -> None:
    update_user_billing(
        user_id,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        plan=plan,
        subscription_status=subscription_status,
        updated_at=_utc_now_iso(),
        clear_subscription_id=clear_subscription_id,
    )


def _create_stripe_checkout_session(**kwargs: Any) -> Any:
    try:
        import stripe
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="Dependencia stripe nao instalada",
        ) from exc

    stripe.api_key = _stripe_secret_key()
    return stripe.checkout.Session.create(**kwargs)


def _verify_stripe_signature(payload: bytes, signature_header: str, secret: str) -> None:
    timestamp, provided_signature = _parse_stripe_signature(signature_header)
    tolerance = max(0, int(app_config.STRIPE_WEBHOOK_TOLERANCE_SECONDS))
    if abs(int(time.time()) - timestamp) > tolerance:
        raise HTTPException(status_code=400, detail="Assinatura Stripe expirada")

    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided_signature):
        raise HTTPException(status_code=400, detail="Assinatura Stripe invalida")


def _parse_stripe_signature(header: str) -> tuple[int, str]:
    timestamp = None
    signature = None
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1" and signature is None:
            signature = value
    if timestamp is None or signature is None:
        raise HTTPException(status_code=400, detail="Assinatura Stripe invalida")
    try:
        return int(timestamp), signature
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Assinatura Stripe invalida") from exc


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        return converted if isinstance(converted, dict) else {}
    return {}


def _as_id(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        ident = value.get("id")
        if isinstance(ident, str) and ident.strip():
            return ident.strip()
    return None


def _stripe_secret_key() -> str:
    return str(getattr(app_config, "STRIPE_SECRET_KEY", "") or "").strip()


def _stripe_price_essencial() -> str:
    return str(getattr(app_config, "STRIPE_PRICE_ESSENCIAL", "") or "").strip()


def _stripe_webhook_secret() -> str:
    return str(getattr(app_config, "STRIPE_WEBHOOK_SECRET", "") or "").strip()


def _checkout_success_url() -> str:
    configured = str(getattr(app_config, "STRIPE_CHECKOUT_SUCCESS_URL", "") or "").strip()
    if configured:
        return configured
    return f"{_public_base()}/?billing=success&session_id={{CHECKOUT_SESSION_ID}}"


def _checkout_cancel_url() -> str:
    configured = str(getattr(app_config, "STRIPE_CHECKOUT_CANCEL_URL", "") or "").strip()
    if configured:
        return configured
    return f"{_public_base()}/?billing=cancel"


def _public_base() -> str:
    return str(getattr(app_config, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/") or (
        "http://localhost:8000"
    )


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
