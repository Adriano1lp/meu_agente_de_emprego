from __future__ import annotations

import shutil
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

import config
from config import sanitize_user_id
from database.repository import anonymize_and_purge_user, collect_user_export_payload
from services.auth_users import get_user_by_id

SENSITIVE_EXPORT_KEYS = {
    "password",
    "password_hash",
    "access_token",
    "refresh_token",
    "jwt",
    "token",
    "authorization",
}


def export_current_user(user_id: str) -> dict[str, Any]:
    safe_user_id = sanitize_user_id(user_id)
    user = get_user_by_id(safe_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")

    payload = collect_user_export_payload(safe_user_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")

    _assert_no_sensitive_keys(payload)
    payload["exported_at"] = _utc_now_iso()
    return payload


def delete_current_user(user_id: str) -> dict[str, Any]:
    safe_user_id = sanitize_user_id(user_id)
    user = get_user_by_id(safe_user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")

    _purge_user_files(safe_user_id)
    deleted_at = _utc_now_iso()
    purged = anonymize_and_purge_user(safe_user_id, deleted_at=deleted_at)
    if not purged:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")

    return {
        "user_id": safe_user_id,
        "deleted": True,
        "deleted_at": deleted_at,
    }


def _purge_user_files(user_id: str) -> None:
    user_dir = config.USERS_DIR / user_id
    if user_dir.exists():
        shutil.rmtree(user_dir, ignore_errors=True)


def _assert_no_sensitive_keys(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in SENSITIVE_EXPORT_KEYS:
                raise HTTPException(
                    status_code=500,
                    detail="Exportacao bloqueada: dado sensivel no pacote",
                )
            _assert_no_sensitive_keys(value)
        return
    if isinstance(payload, list):
        for item in payload:
            _assert_no_sensitive_keys(item)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
