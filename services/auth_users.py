from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from config import sanitize_user_id
from database.repository import (
    create_user,
    get_user_by_email,
    get_user_by_id as find_user_by_id,
)

PBKDF2_ITERATIONS = 200_000


def register_user(
    *,
    display_name: str,
    email: str,
    password: str,
) -> dict[str, Any]:
    normalized_email = _normalize_email(email)
    display_name = display_name.strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="Nome obrigatorio")

    _validate_password(password)

    if get_user_by_email(normalized_email):
        raise HTTPException(status_code=409, detail="Email ja cadastrado")

    user_id = _build_user_id(normalized_email)
    now = _utc_now_iso()
    user = {
        "user_id": user_id,
        "email": normalized_email,
        "display_name": display_name,
        "password_hash": _hash_password(password),
        "created_at": now,
        "updated_at": now,
    }
    try:
        create_user(user)
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


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    safe_user_id = sanitize_user_id(user_id)
    user = find_user_by_id(safe_user_id)
    return _public_user(user) if user else None


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
    return sanitize_user_id(f"user_{digest}")


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


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
