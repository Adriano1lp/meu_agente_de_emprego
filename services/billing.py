from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from config import (
    ESSENCIAL_LLM_QUOTA_MONTHLY,
    FREE_LLM_QUOTA_MONTHLY,
    PLAN_ESSENCIAL,
    PLAN_FREE,
    PUBLIC_BASE_URL,
    STRIPE_CANCEL_URL,
    STRIPE_PRICE_ESSENCIAL,
    STRIPE_SECRET_KEY,
    STRIPE_SUCCESS_URL,
    STRIPE_WEBHOOK_SECRET,
    STRIPE_WEBHOOK_TOLERANCE_SECONDS,
    sanitize_user_id,
)
from database.repository import (
    append_usage_event,
    count_usage_units,
    get_subscription_by_stripe_id,
    get_user_subscription,
    upsert_user_subscription,
)
from services.auth_users import get_user_by_id

ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}


def get_entitlement(user_id: str) -> dict[str, Any]:
    safe_user_id = sanitize_user_id(user_id)
    subscription = get_user_subscription(safe_user_id)
    plan = PLAN_FREE
    status = "active"
    if (
        subscription
        and subscription.get("plan") == PLAN_ESSENCIAL
        and str(subscription.get("status") or "") in ACTIVE_SUBSCRIPTION_STATUSES
    ):
        plan = PLAN_ESSENCIAL
        status = str(subscription["status"])
    elif subscription:
        status = str(subscription.get("status") or "inactive")

    period = current_usage_period()
    used = count_usage_units(safe_user_id, period)
    quota = monthly_quota_for_plan(plan)
    return {
        "user_id": safe_user_id,
        "plan": plan,
        "status": status,
        "period": period,
        "used": used,
        "quota": quota,
        "remaining": max(0, quota - used),
        "price_brl": 19.90 if plan == PLAN_ESSENCIAL else 0,
        "stripe_customer_id": (subscription or {}).get("stripe_customer_id"),
        "stripe_subscription_id": (subscription or {}).get("stripe_subscription_id"),
    }


def consume_llm_quota(user_id: str, feature: str) -> dict[str, Any]:
    entitlement = get_entitlement(user_id)
    if entitlement["used"] >= entitlement["quota"]:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "quota_exceeded",
                "message": (
                    "Cota mensal de uso da IA esgotada. "
                    "Assine o plano Essencial para continuar."
                ),
                "plan": entitlement["plan"],
                "used": entitlement["used"],
                "quota": entitlement["quota"],
                "period": entitlement["period"],
            },
        )

    append_usage_event(
        {
            "user_id": entitlement["user_id"],
            "feature": feature,
            "units": 1,
            "period": entitlement["period"],
            "created_at": _utc_now_iso(),
        }
    )
    entitlement["used"] += 1
    entitlement["remaining"] = max(0, entitlement["quota"] - entitlement["used"])
    return entitlement


def create_checkout_session(user_id: str) -> dict[str, str]:
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ESSENCIAL:
        raise HTTPException(status_code=503, detail="Billing Stripe nao configurado")

    safe_user_id = sanitize_user_id(user_id)
    user = get_user_by_id(safe_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")

    try:
        import stripe
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="Dependencia stripe nao instalada",
        ) from exc

    stripe.api_key = STRIPE_SECRET_KEY
    success_url = STRIPE_SUCCESS_URL or f"{_public_base()}/?billing=success"
    cancel_url = STRIPE_CANCEL_URL or f"{_public_base()}/?billing=cancel"
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_ESSENCIAL, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=safe_user_id,
        customer_email=user["email"],
        metadata={"user_id": safe_user_id, "plan": PLAN_ESSENCIAL},
        subscription_data={"metadata": {"user_id": safe_user_id, "plan": PLAN_ESSENCIAL}},
    )
    checkout_url = getattr(session, "url", None)
    session_id = getattr(session, "id", None)
    if not checkout_url or not session_id:
        raise HTTPException(status_code=502, detail="Stripe nao retornou checkout session")
    return {
        "checkout_url": str(checkout_url),
        "session_id": str(session_id),
    }


def handle_stripe_webhook(payload: bytes, signature_header: str | None) -> dict[str, Any]:
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="STRIPE_WEBHOOK_SECRET nao configurado")
    if not signature_header:
        raise HTTPException(status_code=400, detail="Assinatura Stripe ausente")

    _verify_stripe_signature(payload, signature_header)
    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Payload Stripe invalido") from exc

    event_type = str(event.get("type") or "")
    data_object = (event.get("data") or {}).get("object") or {}
    if event_type == "checkout.session.completed":
        _apply_checkout_session(data_object)
    elif event_type in {
        "customer.subscription.updated",
        "customer.subscription.created",
    }:
        _apply_subscription_object(data_object)
    elif event_type in {"customer.subscription.deleted", "customer.subscription.canceled"}:
        _apply_subscription_object({**data_object, "status": "canceled"})

    return {"received": True, "type": event_type}


def monthly_quota_for_plan(plan: str) -> int:
    if plan == PLAN_ESSENCIAL:
        return ESSENCIAL_LLM_QUOTA_MONTHLY
    return FREE_LLM_QUOTA_MONTHLY


def current_usage_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _apply_checkout_session(session: dict[str, Any]) -> None:
    metadata = session.get("metadata") or {}
    user_id = metadata.get("user_id") or session.get("client_reference_id")
    if not user_id:
        return
    _upsert_paid_subscription(
        user_id=str(user_id),
        status="active",
        stripe_customer_id=_as_id(session.get("customer")),
        stripe_subscription_id=_as_id(session.get("subscription")),
        stripe_price_id=STRIPE_PRICE_ESSENCIAL or None,
        current_period_start=None,
        current_period_end=None,
    )


def _apply_subscription_object(subscription: dict[str, Any]) -> None:
    metadata = subscription.get("metadata") or {}
    user_id = metadata.get("user_id")
    if not user_id:
        existing = _find_subscription_by_stripe_id(_as_id(subscription.get("id")))
        user_id = (existing or {}).get("user_id")
    if not user_id:
        return

    status = str(subscription.get("status") or "inactive")
    plan = (
        PLAN_ESSENCIAL
        if status in ACTIVE_SUBSCRIPTION_STATUSES
        else PLAN_FREE
    )
    price_id = _extract_price_id(subscription) or STRIPE_PRICE_ESSENCIAL or None
    _upsert_paid_subscription(
        user_id=str(user_id),
        status=status,
        stripe_customer_id=_as_id(subscription.get("customer")),
        stripe_subscription_id=_as_id(subscription.get("id")) or _as_id(
            subscription.get("subscription")
        ),
        stripe_price_id=price_id,
        current_period_start=_unix_to_iso(subscription.get("current_period_start")),
        current_period_end=_unix_to_iso(subscription.get("current_period_end")),
        plan=plan,
    )


def _upsert_paid_subscription(
    *,
    user_id: str,
    status: str,
    stripe_customer_id: str | None,
    stripe_subscription_id: str | None,
    stripe_price_id: str | None,
    current_period_start: str | None,
    current_period_end: str | None,
    plan: str = PLAN_ESSENCIAL,
) -> None:
    previous = get_user_subscription(user_id) or {}
    upsert_user_subscription(
        {
            "user_id": sanitize_user_id(user_id),
            "plan": plan,
            "status": status,
            "stripe_customer_id": stripe_customer_id or previous.get("stripe_customer_id"),
            "stripe_subscription_id": (
                stripe_subscription_id or previous.get("stripe_subscription_id")
            ),
            "stripe_price_id": stripe_price_id or previous.get("stripe_price_id"),
            "current_period_start": current_period_start,
            "current_period_end": current_period_end,
            "updated_at": _utc_now_iso(),
        }
    )


def _find_subscription_by_stripe_id(subscription_id: str | None) -> dict[str, Any] | None:
    if not subscription_id:
        return None
    return get_subscription_by_stripe_id(subscription_id)


def _verify_stripe_signature(payload: bytes, signature_header: str) -> None:
    timestamp, provided_signature = _parse_stripe_signature(signature_header)
    if abs(int(time.time()) - timestamp) > STRIPE_WEBHOOK_TOLERANCE_SECONDS:
        raise HTTPException(status_code=400, detail="Assinatura Stripe expirada")

    signed_payload = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(
        STRIPE_WEBHOOK_SECRET.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
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


def _extract_price_id(subscription: dict[str, Any]) -> str | None:
    items = (subscription.get("items") or {}).get("data") or []
    if items:
        price = items[0].get("price") or {}
        return _as_id(price.get("id") or price)
    return None


def _as_id(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, dict):
        ident = value.get("id")
        if isinstance(ident, str) and ident.strip():
            return ident
    return None


def _unix_to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), UTC).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _public_base() -> str:
    return PUBLIC_BASE_URL or "http://localhost:8000"


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
