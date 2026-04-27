from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from config import AUTH_USERS_FILE, sanitize_user_id

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

    users = _load_users()
    if normalized_email in users:
        raise HTTPException(status_code=409, detail="Email ja cadastrado")

    user_id = _build_user_id(normalized_email)
    now = _utc_now_iso()
    users[normalized_email] = {
        "user_id": user_id,
        "email": normalized_email,
        "display_name": display_name,
        "password_hash": _hash_password(password),
        "created_at": now,
        "updated_at": now,
    }
    _save_users(users)

    return _public_user(users[normalized_email])


def authenticate_user(email: str, password: str) -> dict[str, Any]:
    normalized_email = _normalize_email(email)
    users = _load_users()
    user = users.get(normalized_email)
    if not user or not _verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Email ou senha invalidos")

    return _public_user(user)


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    safe_user_id = sanitize_user_id(user_id)
    for user in _load_users().values():
        if user.get("user_id") == safe_user_id:
            return _public_user(user)
    return None


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


def _load_users() -> dict[str, dict[str, Any]]:
    if not AUTH_USERS_FILE.exists():
        return {}

    try:
        data = json.loads(AUTH_USERS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Base de usuarios invalida") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="Base de usuarios invalida")

    return {
        str(email): record
        for email, record in data.items()
        if isinstance(record, dict)
    }


def _save_users(users: dict[str, dict[str, Any]]) -> None:
    AUTH_USERS_FILE.write_text(
        json.dumps(users, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


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
