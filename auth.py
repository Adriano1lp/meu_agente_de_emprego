from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Header, HTTPException

from config import AUTH_MODE, JWT_EXPIRATION_MINUTES, JWT_SECRET, sanitize_user_id


def get_current_user_id(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    if authorization and authorization.strip():
        return _get_user_id_from_jwt_header(authorization)

    if AUTH_MODE == "jwt":
        raise HTTPException(
            status_code=401,
            detail="Header Authorization obrigatorio",
        )

    return _get_user_id_from_custom_header(x_user_id)


def create_access_token(
    *,
    user_id: str,
    email: str,
    display_name: str,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sanitize_user_id(user_id),
        "user_id": sanitize_user_id(user_id),
        "email": email.strip().lower(),
        "display_name": display_name.strip(),
        "iat": now,
        "exp": now + JWT_EXPIRATION_MINUTES * 60,
    }
    return _encode_jwt(payload)


def decode_access_token(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="JWT invalido")

    header_segment, payload_segment, signature_segment = parts
    signed_data = f"{header_segment}.{payload_segment}".encode("ascii")
    expected_signature = _sign(signed_data)
    provided_signature = _urlsafe_b64decode(signature_segment)

    if not hmac.compare_digest(expected_signature, provided_signature):
        raise HTTPException(status_code=401, detail="Assinatura JWT invalida")

    payload = _decode_segment(payload_segment, "Payload JWT invalido")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="Payload JWT invalido")

    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        raise HTTPException(status_code=401, detail="Token expirado")

    return payload


def _get_user_id_from_custom_header(x_user_id: str | None) -> str:
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(
            status_code=401,
            detail="Header X-User-Id obrigatorio",
        )

    try:
        return sanitize_user_id(x_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _get_user_id_from_jwt_header(authorization: str | None) -> str:
    if not authorization or not authorization.strip():
        raise HTTPException(
            status_code=401,
            detail="Header Authorization obrigatorio",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="Authorization deve usar Bearer token",
        )

    payload = decode_access_token(token.strip())
    user_id = payload.get("user_id") or payload.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(
            status_code=401,
            detail="Token sem claim user_id ou sub",
        )

    try:
        return sanitize_user_id(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _encode_jwt(payload: dict[str, Any]) -> str:
    header = {
        "alg": "HS256",
        "typ": "JWT",
    }
    header_segment = _encode_segment(header)
    payload_segment = _encode_segment(payload)
    signed_data = f"{header_segment}.{payload_segment}".encode("ascii")
    signature_segment = _urlsafe_b64encode(_sign(signed_data))
    return f"{header_segment}.{payload_segment}.{signature_segment}"


def _sign(data: bytes) -> bytes:
    return hmac.new(
        JWT_SECRET.encode("utf-8"),
        data,
        hashlib.sha256,
    ).digest()


def _encode_segment(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return _urlsafe_b64encode(raw)


def _decode_segment(segment: str, error_message: str) -> Any:
    try:
        raw = _urlsafe_b64decode(segment)
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=401, detail=error_message) from exc


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
