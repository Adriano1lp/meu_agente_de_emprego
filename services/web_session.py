from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

from fastapi import HTTPException

import config
from auth import create_access_token, decode_access_token
from database.repository import (
    create_refresh_token,
    get_refresh_token_by_hash,
    revoke_refresh_families_for_user,
    revoke_refresh_family,
    rotate_refresh_token,
)
from services.auth_users import get_user_by_id

logger = logging.getLogger(__name__)

REFRESH_COOKIE_NAME = "__Secure-mae_rt"
REFRESH_COOKIE_PATH = "/auth"
_INVALID_SESSION = "Sessao invalida"


def cookie_session_requested(session: str | None, client_header: str | None) -> bool:
    if (session or "").strip() == "cookie":
        return True
    return (client_header or "").strip() == "web"


def enforce_cookie_csrf(origin: str | None, csrf_header: str | None) -> None:
    """Origin exato na allowlist e header custom. Sem Origin é 403.

    '*' nao entra na allowlist do modo cookie: nao ha origem exata para casar.
    """
    allowed = [
        item.strip()
        for item in config.CORS_ALLOW_ORIGINS
        if item.strip() and item.strip() != "*"
    ]
    if not origin or origin not in allowed:
        raise HTTPException(status_code=403, detail="Origin nao permitida")
    if csrf_header != "1":
        raise HTTPException(status_code=403, detail="Header X-MAE-CSRF obrigatorio")


def refresh_cookie_max_age() -> int:
    return int(config.REFRESH_TOKEN_IDLE_DAYS) * 24 * 60 * 60


def refresh_cookie_header(value: str, max_age: int) -> str:
    # Header manual: Starlette so aceita Partitioned no Python 3.14+.
    # Sem Domain, o cookie fica host-only.
    return (
        f"{REFRESH_COOKIE_NAME}={value}; "
        f"Max-Age={max_age}; Path={REFRESH_COOKIE_PATH}; "
        "Secure; HttpOnly; SameSite=None; Partitioned"
    )


def clear_refresh_cookie_header() -> str:
    return refresh_cookie_header("", 0)


def issue_web_session(user: dict[str, Any]) -> tuple[dict[str, Any], str]:
    raw_token = secrets.token_urlsafe(32)
    now = _utc_now()
    family_expires_at = now + timedelta(days=int(config.REFRESH_FAMILY_MAX_DAYS))
    idle_expires_at = now + timedelta(days=int(config.REFRESH_TOKEN_IDLE_DAYS))
    create_refresh_token(
        {
            "token_hash": _hash_refresh_token(raw_token),
            "family_id": str(uuid.uuid4()),
            "user_id": user["user_id"],
            "created_at": _iso(now),
            "expires_at": _iso(idle_expires_at),
            "family_expires_at": _iso(family_expires_at),
            "used_at": None,
            "replaced_by_hash": None,
            "revoked_at": None,
            "revoke_reason": None,
        }
    )
    body = _access_body(user)
    body["user"] = user
    return body, raw_token


def refresh_web_session(raw_token: str | None) -> tuple[dict[str, Any], str]:
    """Rotaciona o refresh. Servidor estrito: reuso revoga a familia, sem folga."""
    presented = (raw_token or "").strip()
    if not presented or _is_access_jwt(presented):
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)

    presented_hash = _hash_refresh_token(presented)
    existing = get_refresh_token_by_hash(presented_hash)
    if not existing:
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)

    now = _utc_now()
    now_iso = _iso(now)
    user_id = str(existing["user_id"])
    if get_user_by_id(user_id) is None:
        _revoke_user(user_id, now_iso, "user_inactive")
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)

    new_raw = secrets.token_urlsafe(32)
    new_hash = _hash_refresh_token(new_raw)
    new_expires_at = _iso(now + timedelta(days=int(config.REFRESH_TOKEN_IDLE_DAYS)))
    rotated = rotate_refresh_token(
        presented_hash=presented_hash,
        new_hash=new_hash,
        now_iso=now_iso,
        new_expires_at=new_expires_at,
        created_at=now_iso,
    )
    if rotated is None:
        _reject_presented_token(presented_hash, now)

    user = get_user_by_id(str(rotated["user_id"]))
    if user is None:
        _revoke_user(str(rotated["user_id"]), now_iso, "user_inactive")
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)

    logger.info(
        "refresh_rotated user_id=%s family_id=%s",
        user["user_id"],
        rotated["family_id"],
    )
    return _access_body(user), new_raw


def logout_web_session(raw_token: str | None) -> None:
    presented = (raw_token or "").strip()
    if not presented or _is_access_jwt(presented):
        return

    row = get_refresh_token_by_hash(_hash_refresh_token(presented))
    if not row:
        return

    revoke_refresh_family(
        family_id=str(row["family_id"]),
        revoked_at=_iso(_utc_now()),
        reason="logout",
    )
    logger.info(
        "logout user_id=%s family_id=%s",
        row["user_id"],
        row["family_id"],
    )


def _reject_presented_token(presented_hash: str, now: datetime) -> NoReturn:
    row = get_refresh_token_by_hash(presented_hash)
    if not row:
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)

    user_id = str(row["user_id"])
    family_id = str(row["family_id"])
    now_iso = _iso(now)
    if get_user_by_id(user_id) is None:
        _revoke_user(user_id, now_iso, "user_inactive")
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)

    if row.get("used_at") or row.get("revoked_at"):
        logger.warning(
            "refresh_reuse_detected user_id=%s family_id=%s",
            user_id,
            family_id,
        )
        revoke_refresh_family(
            family_id=family_id,
            revoked_at=now_iso,
            reason="reuse",
        )
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)

    if _is_expired(row, now):
        revoke_refresh_family(
            family_id=family_id,
            revoked_at=now_iso,
            reason="expired",
        )
        raise HTTPException(status_code=401, detail=_INVALID_SESSION)

    raise HTTPException(status_code=401, detail=_INVALID_SESSION)


def _access_body(user: dict[str, Any]) -> dict[str, Any]:
    minutes = int(config.WEB_ACCESS_TOKEN_MINUTES)
    access_token = create_access_token(
        user_id=user["user_id"],
        email=user["email"],
        display_name=user["display_name"],
        expires_in_minutes=minutes,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": minutes * 60,
    }


def _revoke_user(user_id: str, revoked_at: str, reason: str) -> None:
    revoke_refresh_families_for_user(
        user_id=user_id,
        revoked_at=revoked_at,
        reason=reason,
    )


def _is_access_jwt(raw_token: str) -> bool:
    try:
        decode_access_token(raw_token)
    except HTTPException:
        return False
    return True


def _is_expired(row: dict[str, Any], now: datetime) -> bool:
    return _parse_iso(str(row["expires_at"])) <= now or _parse_iso(
        str(row["family_expires_at"])
    ) <= now


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()
