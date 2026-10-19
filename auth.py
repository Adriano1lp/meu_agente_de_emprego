from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import Header, HTTPException

import config
from config import AUTH_MODE, JWT_EXPIRATION_MINUTES, JWT_SECRET, sanitize_user_id

# Verificador fixo em HS256. O alg do header nao escolhe o algoritmo.
_ALLOWED_JWT_ALGORITHMS = ("HS256",)


def get_bearer_user_id(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """Identifica o usuário só pelo Bearer JWT. Ignora X-User-Id."""
    if authorization is None or not authorization.strip():
        raise HTTPException(
            status_code=401,
            detail="Header Authorization obrigatorio",
        )
    return _get_user_id_from_jwt_header(authorization)


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
    expires_in_minutes: int | None = None,
) -> str:
    minutes = JWT_EXPIRATION_MINUTES if expires_in_minutes is None else expires_in_minutes
    now = int(time.time())
    payload = {
        "sub": sanitize_user_id(user_id),
        "user_id": sanitize_user_id(user_id),
        "email": email.strip().lower(),
        "display_name": display_name.strip(),
        "typ": "access",
        "iat": now,
        "exp": now + minutes * 60,
    }
    return _encode_jwt(payload)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decodifica um access JWT. Qualquer token malformado vira 401, nunca 500.

    Um unico try cobre split, ascii, base64, json e hmac. Refresh opaco nao
    autentica como Bearer. Sem a claim typ, o JWT legado so passa com a flag
    ligada e, se houver corte, com iat anterior a ele.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3 or any(not part for part in parts):
            raise HTTPException(status_code=401, detail="JWT invalido")

        header_segment, payload_segment, signature_segment = parts
        signed_data = f"{header_segment}.{payload_segment}".encode("ascii")
        header = json.loads(_urlsafe_b64decode(header_segment))
        if (
            not isinstance(header, dict)
            or header.get("alg") not in _ALLOWED_JWT_ALGORITHMS
            or header.get("typ") != "JWT"
        ):
            raise HTTPException(status_code=401, detail="JWT invalido")

        expected_signature = _sign(signed_data)
        provided_signature = _urlsafe_b64decode(signature_segment)
        if not hmac.compare_digest(expected_signature, provided_signature):
            raise HTTPException(status_code=401, detail="JWT invalido")

        payload = json.loads(_urlsafe_b64decode(payload_segment).decode("utf-8"))
        if not isinstance(payload, dict):
            raise HTTPException(status_code=401, detail="JWT invalido")
        # typ diferente de "access" e sempre 401. Sem typ, so o legado
        # temporario: flag ligada e iat anterior ao corte, se o corte existir.
        if not _payload_typ_permitido(payload):
            raise HTTPException(status_code=401, detail="JWT invalido")

        exp = payload.get("exp")
        if isinstance(exp, bool) or not isinstance(exp, int):
            raise HTTPException(status_code=401, detail="JWT invalido")
        if exp < int(time.time()):
            raise HTTPException(status_code=401, detail="Token expirado")
        return payload
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="JWT invalido") from None


_CORTE_AUSENTE = object()
_CORTE_INVALIDO = object()


def _payload_typ_permitido(payload: dict[str, Any]) -> bool:
    if "typ" in payload:
        return payload.get("typ") == "access"
    return _legado_sem_typ_permitido(payload)


def _legado_sem_typ_permitido(payload: dict[str, Any]) -> bool:
    if not config.JWT_ACCEPT_LEGACY_UNTYPED:
        return False
    corte = _corte_legado_sem_typ()
    if corte is _CORTE_INVALIDO:
        return False
    if corte is _CORTE_AUSENTE:
        return True
    iat = payload.get("iat")
    if isinstance(iat, bool) or not isinstance(iat, (int, float)):
        return False
    emitido = datetime.fromtimestamp(float(iat), tz=UTC)
    return emitido < corte


def _corte_legado_sem_typ() -> datetime | object:
    bruto = (config.JWT_LEGACY_UNTYPED_CUTOFF_ISO or "").strip()
    if not bruto:
        return _CORTE_AUSENTE
    try:
        parsed = datetime.fromisoformat(bruto)
    except ValueError:
        return _CORTE_INVALIDO
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
