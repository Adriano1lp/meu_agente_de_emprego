from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from config import (
    LEGAL_DOCUMENT_PRIVACY,
    LEGAL_DOCUMENT_TERMS,
    PRIVACY_POLICY_VERSION,
    TERMS_OF_SERVICE_VERSION,
)
from database.repository import append_consent_log


def current_legal_documents() -> dict[str, Any]:
    return {
        "terms_of_service": {
            "id": LEGAL_DOCUMENT_TERMS,
            "version": TERMS_OF_SERVICE_VERSION,
            "title": "Termos de Uso",
        },
        "privacy_policy": {
            "id": LEGAL_DOCUMENT_PRIVACY,
            "version": PRIVACY_POLICY_VERSION,
            "title": "Politica de Privacidade",
        },
    }


def require_signup_legal_acceptance(
    *,
    terms_accepted: bool,
    privacy_accepted: bool,
) -> None:
    if not terms_accepted:
        raise HTTPException(status_code=400, detail="Aceite do termo de uso obrigatorio")
    if not privacy_accepted:
        raise HTTPException(
            status_code=400,
            detail="Aceite da politica de privacidade obrigatorio",
        )


def record_current_legal_acceptance(
    *,
    user_id: str,
    source: str,
    accepted_at: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, str]:
    terms_version = TERMS_OF_SERVICE_VERSION
    privacy_version = PRIVACY_POLICY_VERSION
    common = {
        "user_id": user_id,
        "accepted": True,
        "accepted_at": accepted_at,
        "source": source,
        "ip_address": ip_address,
        "user_agent": user_agent,
    }
    append_consent_log(
        {
            **common,
            "document_type": LEGAL_DOCUMENT_TERMS,
            "document_version": terms_version,
        }
    )
    append_consent_log(
        {
            **common,
            "document_type": LEGAL_DOCUMENT_PRIVACY,
            "document_version": privacy_version,
        }
    )
    return {
        "terms_version": terms_version,
        "privacy_version": privacy_version,
    }


def user_needs_reconsent(user: dict[str, Any] | None) -> bool:
    if not user:
        return True
    if user.get("password_hash") == "legacy_external_auth":
        return False
    if not user.get("terms_accepted"):
        return True
    if not user.get("privacy_accepted"):
        return bool(user.get("terms_version") or user.get("privacy_version"))
    return (
        str(user.get("terms_version") or "") != TERMS_OF_SERVICE_VERSION
        or str(user.get("privacy_version") or "") != PRIVACY_POLICY_VERSION
    )
