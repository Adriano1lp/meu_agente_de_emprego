from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from config import (
    CURRENT_PRIVACY_VERSION,
    CURRENT_TERMS_VERSION,
    ENVIRONMENT,
    PASSWORD_RESET_EXPIRATION_MINUTES,
    PASSWORD_RESET_EXPOSE_TOKEN,
    sanitize_user_id,
)
from database.repository import (
    append_consent_log,
    create_password_reset_token,
    create_user,
    get_password_reset_token_by_hash,
    get_user_by_email,
    get_user_by_id as find_user_by_id,
    mark_password_reset_token_used,
    update_user_consent,
    update_user_password_hash,
    user_id_exists,
)
from services.legal import current_version_for

PBKDF2_ITERATIONS = 200_000
PASSWORD_RESET_GENERIC_MESSAGE = (
    "Se o email estiver cadastrado, enviaremos instrucoes para recuperar a senha."
)


def register_user(
    *,
    display_name: str,
    email: str,
    password: str,
    terms_accepted: bool | None,
    terms_version: str | None,
    privacy_accepted: bool | None,
    privacy_version: str | None,
) -> dict[str, Any]:
    normalized_email = _normalize_email(email)
    display_name = display_name.strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="Nome obrigatorio")

    _validate_password(password)
    _require_signup_consents(
        terms_accepted=terms_accepted,
        terms_version=terms_version,
        privacy_accepted=privacy_accepted,
        privacy_version=privacy_version,
    )

    if get_user_by_email(normalized_email):
        raise HTTPException(status_code=409, detail="Email ja cadastrado")

    user_id = _build_user_id(normalized_email)
    now = _utc_now_iso()
    user = {
        "user_id": user_id,
        "email": normalized_email,
        "display_name": display_name,
        "password_hash": _hash_password(password),
        "terms_accepted": True,
        "terms_accepted_at": now,
        "terms_version": CURRENT_TERMS_VERSION,
        "privacy_accepted": True,
        "privacy_accepted_at": now,
        "privacy_version": CURRENT_PRIVACY_VERSION,
        "created_at": now,
        "updated_at": now,
    }
    consents = [
        {
            "user_id": user_id,
            "doc": "terms",
            "version": CURRENT_TERMS_VERSION,
            "accepted_at": now,
        },
        {
            "user_id": user_id,
            "doc": "privacy",
            "version": CURRENT_PRIVACY_VERSION,
            "accepted_at": now,
        },
    ]
    try:
        create_user(user, consents=consents)
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Email ja cadastrado") from exc
        raise

    return _public_user(user)


def authenticate_user(email: str, password: str) -> dict[str, Any]:
    normalized_email = _normalize_email(email)
    user = get_user_by_email(normalized_email)
    if not user or not _verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email ou senha invalidos")

    return _public_user(user)


def request_password_reset(email: str) -> dict[str, Any]:
    normalized_email = _normalize_email(email)
    user = get_user_by_email(normalized_email)
    response: dict[str, Any] = {"message": PASSWORD_RESET_GENERIC_MESSAGE}

    if not user:
        return response

    token = secrets.token_urlsafe(32)
    now = _utc_now()
    expires_at = now + timedelta(minutes=PASSWORD_RESET_EXPIRATION_MINUTES)
    create_password_reset_token(
        {
            "user_id": user["user_id"],
            "email": normalized_email,
            "token_hash": _hash_reset_token(token),
            "expires_at": expires_at.replace(microsecond=0).isoformat(),
            "created_at": now.replace(microsecond=0).isoformat(),
            "used_at": None,
        }
    )

    if PASSWORD_RESET_EXPOSE_TOKEN or ENVIRONMENT not in {"production", "prod"}:
        response["reset_token"] = token

    return response


def confirm_password_reset(token: str, new_password: str) -> dict[str, str]:
    token = token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token de recuperacao obrigatorio")

    _validate_password(new_password)
    reset_token = get_password_reset_token_by_hash(_hash_reset_token(token))
    if not reset_token:
        raise HTTPException(status_code=400, detail="Token de recuperacao invalido ou expirado")

    if reset_token.get("used_at"):
        raise HTTPException(status_code=400, detail="Token de recuperacao invalido ou expirado")

    expires_at = _parse_iso_datetime(str(reset_token["expires_at"]))
    if expires_at <= _utc_now():
        raise HTTPException(status_code=400, detail="Token de recuperacao invalido ou expirado")

    user_id = str(reset_token["user_id"])
    now = _utc_now_iso()
    updated_user = update_user_password_hash(
        user_id=user_id,
        password_hash=_hash_password(new_password),
        updated_at=now,
    )
    if not updated_user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")

    mark_password_reset_token_used(
        token_hash=str(reset_token["token_hash"]),
        used_at=now,
    )
    return {"message": "Senha redefinida com sucesso"}


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    safe_user_id = sanitize_user_id(user_id)
    user = find_user_by_id(safe_user_id)
    return _public_user(user) if user else None


def user_can_access_terms_protected_routes(user_id: str) -> bool | None:
    safe_user_id = sanitize_user_id(user_id)
    user = find_user_by_id(safe_user_id)
    if not user:
        return None
    if user.get("password_hash") == "legacy_external_auth":
        return True
    return get_outdated_consent_code(user_id) is None


def get_outdated_consent_code(user_id: str) -> str | None:
    safe_user_id = sanitize_user_id(user_id)
    user = find_user_by_id(safe_user_id)
    if not user:
        return None
    if user.get("password_hash") == "legacy_external_auth":
        return None
    if str(user.get("terms_version") or "") != CURRENT_TERMS_VERSION:
        return "TERMS_OUTDATED"
    if str(user.get("privacy_version") or "") != CURRENT_PRIVACY_VERSION:
        return "PRIVACY_OUTDATED"
    return None


def accept_terms_for_user(user_id: str) -> dict[str, Any]:
    return accept_consent_for_user(
        user_id,
        doc="terms",
        version=CURRENT_TERMS_VERSION,
    )


def accept_consent_for_user(user_id: str, *, doc: str, version: str) -> dict[str, Any]:
    safe_user_id = sanitize_user_id(user_id)
    user = find_user_by_id(safe_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")

    normalized_doc = (doc or "").strip().lower()
    if normalized_doc not in {"terms", "privacy"}:
        raise HTTPException(status_code=400, detail="doc deve ser terms ou privacy")

    normalized_version = (version or "").strip()
    current_version = current_version_for(normalized_doc)
    if not normalized_version:
        raise HTTPException(status_code=400, detail="version obrigatoria")
    if normalized_version != current_version:
        raise HTTPException(
            status_code=400,
            detail=f"Versao invalida. Vigente: {current_version}",
        )

    now = _utc_now_iso()
    append_consent_log(
        {
            "user_id": safe_user_id,
            "doc": normalized_doc,
            "version": normalized_version,
            "accepted_at": now,
        }
    )
    updated_user = update_user_consent(
        safe_user_id,
        doc=normalized_doc,
        version=normalized_version,
        accepted_at=now,
    )
    if not updated_user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")
    return _public_user(updated_user)


def _require_signup_consents(
    *,
    terms_accepted: bool | None,
    terms_version: str | None,
    privacy_accepted: bool | None,
    privacy_version: str | None,
) -> None:
    missing = any(
        value is None
        for value in (terms_accepted, terms_version, privacy_accepted, privacy_version)
    )
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "Aceite de termos e privacidade obrigatorio "
                "(terms_accepted, terms_version, privacy_accepted, privacy_version)"
            ),
        )

    if terms_accepted is not True:
        raise HTTPException(status_code=400, detail="Aceite do termo de uso obrigatorio")
    if privacy_accepted is not True:
        raise HTTPException(
            status_code=400,
            detail="Aceite da politica de privacidade obrigatorio",
        )

    normalized_terms_version = str(terms_version).strip()
    normalized_privacy_version = str(privacy_version).strip()
    if not normalized_terms_version or not normalized_privacy_version:
        raise HTTPException(
            status_code=400,
            detail=(
                "Aceite de termos e privacidade obrigatorio "
                "(terms_accepted, terms_version, privacy_accepted, privacy_version)"
            ),
        )
    if normalized_terms_version != CURRENT_TERMS_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Versao de termos invalida. Vigente: {CURRENT_TERMS_VERSION}",
        )
    if normalized_privacy_version != CURRENT_PRIVACY_VERSION:
        raise HTTPException(
            status_code=400,
            detail=f"Versao de privacidade invalida. Vigente: {CURRENT_PRIVACY_VERSION}",
        )


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized or "." not in normalized.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Email invalido")
    return normalized


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(
            status_code=400,
            detail="A senha deve ter pelo menos 8 caracteres",
        )


def _build_user_id(email: str) -> str:
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]
    candidate = sanitize_user_id(f"user_{digest}")
    if not user_id_exists(candidate):
        return candidate
    return sanitize_user_id(f"user_{digest}_{secrets.token_hex(4)}")


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${PBKDF2_ITERATIONS}$"
        f"{base64.urlsafe_b64encode(salt).decode('ascii')}$"
        f"{base64.urlsafe_b64encode(digest).decode('ascii')}"
    )


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iteration_text, salt_text, digest_text = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iteration_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected_digest = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except Exception:
        return False

    computed_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(computed_digest, expected_digest)


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "terms_accepted": bool(user.get("terms_accepted")),
        "terms_accepted_at": user.get("terms_accepted_at"),
        "terms_version": user.get("terms_version"),
        "privacy_accepted": bool(user.get("privacy_accepted")),
        "privacy_accepted_at": user.get("privacy_accepted_at"),
        "privacy_version": user.get("privacy_version"),
        "plan": user.get("plan") or "free",
        "subscription_status": user.get("subscription_status") or "none",
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat()
