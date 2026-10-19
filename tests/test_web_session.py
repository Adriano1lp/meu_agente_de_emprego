from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import ModuleType
from unittest.mock import MagicMock

import anyio
import httpx
import pytest

import auth
from config import (
    CURRENT_PRIVACY_VERSION,
    CURRENT_TERMS_VERSION,
    JWT_EXPIRATION_MINUTES,
    REFRESH_TOKEN_IDLE_DAYS,
    WEB_ACCESS_TOKEN_MINUTES,
)

ORIGIN = "https://web.example"
EVIL_ORIGIN = "https://evil.example"
PASSWORD = "senha-forte-123"


def _install_heavy_service_stubs() -> None:
    stubs = {
        "services.main_chat": {
            "generate_cover_letter": MagicMock(),
            "pipeline_with_details": MagicMock(),
        },
        "services.main_carta": {"gerar_pdf_carta_apresentacao": MagicMock()},
        "services.main_curriculo": {"gerar_pdf_profissional": MagicMock()},
        "services.main_rag": {"rebuild_vectorstore_for_user": MagicMock()},
        "services.development_plan": {
            "DEFAULT_ANALYSIS_LIMIT": 10,
            "MAX_ANALYSIS_LIMIT": 20,
            "generate_development_plan": MagicMock(),
            "read_active_development_plan": MagicMock(),
            "read_development_plan_history": MagicMock(),
            "update_development_plan_item_status": MagicMock(),
        },
        "services.user_data": {
            "get_user_profile": MagicMock(),
            "save_manual_profile": MagicMock(),
            "save_user_cv": MagicMock(),
            "save_user_profile": MagicMock(),
        },
    }
    for name, attributes in stubs.items():
        if name in sys.modules:
            continue
        module = ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[name] = module


def _load_app():
    _install_heavy_service_stubs()
    from main import app

    return app


def _client():
    from fastapi.testclient import TestClient

    return TestClient(_load_app())


def _allow_web_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.CORS_ALLOW_ORIGINS", [ORIGIN])


def _csrf_headers(*, origin: str = ORIGIN, csrf: str = "1") -> dict[str, str]:
    return {"Origin": origin, "X-MAE-CSRF": csrf}


def _consent() -> dict[str, object]:
    return {
        "terms_accepted": True,
        "terms_version": CURRENT_TERMS_VERSION,
        "privacy_accepted": True,
        "privacy_version": CURRENT_PRIVACY_VERSION,
    }


def _register(client, email: str, **extra) -> httpx.Response:
    return client.post(
        "/auth/register",
        json={
            "display_name": "Usuario Web",
            "email": email,
            "password": PASSWORD,
            **_consent(),
            **extra,
        },
        headers=extra.pop("_headers", None) or {},
    )


def _login(client, email: str, *, headers: dict[str, str] | None = None, **extra) -> httpx.Response:
    return client.post(
        "/auth/login",
        json={"email": email, "password": PASSWORD, **extra},
        headers=headers or {},
    )


def _set_cookies(response: httpx.Response) -> list[str]:
    return response.headers.get_list("set-cookie")


def _assert_refresh_cookie(header: str, *, max_age: int) -> str:
    name, _, remainder = header.partition(";")
    key, _, value = name.partition("=")
    assert key == "__Secure-mae_rt"
    assert value
    assert f"Max-Age={max_age};" in header
    assert "Path=/auth" in header
    assert "; Secure;" in header
    assert "; HttpOnly;" in header
    assert "SameSite=None" in header
    assert "Partitioned" in header
    lowered = header.lower()
    assert "domain=" not in lowered
    return value


def _assert_cleared_cookie(header: str) -> None:
    name, _, _remainder = header.partition(";")
    key, _, value = name.partition("=")
    assert key == "__Secure-mae_rt"
    assert value == ""
    assert "Max-Age=0;" in header
    assert "Path=/auth" in header
    assert "; Secure;" in header
    assert "; HttpOnly;" in header
    assert "SameSite=None" in header
    assert "Partitioned" in header
    assert "domain=" not in header.lower()


def _rows(db_path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute("SELECT * FROM refresh_tokens"))
    finally:
        connection.close()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64(raw: bytes) -> str:
    return auth._urlsafe_b64encode(raw)


def _signed_token(header: dict, payload_segment: str) -> str:
    header_segment = auth._encode_segment(header)
    signed = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = _b64(auth._sign(signed))
    return f"{header_segment}.{payload_segment}.{signature}"


def _malformed_token(case: str) -> str:
    now = int(time.time())
    if case == "invalid.token.x":
        return "invalid.token.x"
    if case == "duas_partes":
        return "a.b"
    if case == "pontos":
        return "..."
    if case == "vazio":
        return ""
    if case == "nao_ascii":
        return "inválido"
    if case == "parte_nao_ascii":
        return "a.b.ç"
    if case == "alg_none":
        header = _b64(b'{"alg":"none","typ":"JWT"}')
        payload = auth._encode_segment(
            {
                "sub": "user_x",
                "user_id": "user_x",
                "typ": "access",
                "iat": now,
                "exp": now + 60,
            }
        )
        return f"{header}.{payload}.abc"
    if case == "assinatura_trocada":
        token = auth.create_access_token(
            user_id="user_x",
            email="a@b.co",
            display_name="A",
        )
        header, payload, signature = token.split(".")
        raw = bytearray(auth._urlsafe_b64decode(signature))
        raw[0] ^= 0xFF
        return f"{header}.{payload}.{auth._urlsafe_b64encode(bytes(raw))}"
    if case == "expirado":
        return auth._encode_jwt(
            {
                "sub": "user_x",
                "user_id": "user_x",
                "email": "a@b.co",
                "display_name": "A",
                "typ": "access",
                "iat": now - 120,
                "exp": now - 60,
            }
        )
    if case == "payload_nao_json":
        return _signed_token(
            {"alg": "HS256", "typ": "JWT"},
            _b64(b"%%%"),
        )
    if case == "sem_exp":
        return auth._encode_jwt(
            {
                "sub": "user_x",
                "user_id": "user_x",
                "email": "a@b.co",
                "display_name": "A",
                "typ": "access",
                "iat": now,
            }
        )
    if case == "typ_refresh":
        return auth._encode_jwt(
            {
                "sub": "user_x",
                "user_id": "user_x",
                "email": "a@b.co",
                "display_name": "A",
                "typ": "refresh",
                "iat": now,
                "exp": now + 60,
            }
        )
    if case == "opaco":
        return "somente-opaco-sem-pontos"
    if case == "snowman":
        return "token-\u2603"
    raise AssertionError(case)


@pytest.mark.parametrize("opt_in", ["body", "header"])
def test_login_cookie_emite_refresh_e_access_curto(isolated_db, monkeypatch, opt_in: str):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = f"{opt_in}.cookie@example.com"
    assert _register(client, email).status_code == 200

    headers = _csrf_headers()
    extra: dict[str, str] = {}
    if opt_in == "body":
        extra["session"] = "cookie"
    else:
        headers["X-MAE-Client"] = "web"

    response = _login(client, email, headers=headers, **extra)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "user"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == WEB_ACCESS_TOKEN_MINUTES * 60
    assert body["user"]["email"] == email
    payload = auth.decode_access_token(body["access_token"])
    assert payload["typ"] == "access"
    assert payload["exp"] - payload["iat"] == WEB_ACCESS_TOKEN_MINUTES * 60
    assert payload["email"] == email

    cookies = _set_cookies(response)
    assert len(cookies) == 1
    raw = _assert_refresh_cookie(cookies[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 24 * 60 * 60)
    assert raw not in response.text
    rows = _rows(isolated_db)
    assert len(rows) == 1
    assert rows[0]["token_hash"] == _sha256(raw)
    assert raw not in {str(value) for value in dict(rows[0]).values()}
    assert "cache-control" in {key.lower() for key in response.headers}
    assert response.headers["cache-control"] == "no-store"

    me = client.get("/users/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200


def test_login_sem_optin_identico_ao_fluxo_atual(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "app.mobile@example.com"
    registered = _register(client, email)
    assert registered.status_code == 200
    assert set(registered.json()) == {"access_token", "token_type", "user"}
    assert _set_cookies(registered) == []
    assert registered.headers["cache-control"] == "no-store"

    response = _login(
        client,
        email,
        headers={"Origin": ORIGIN, "X-MAE-Client": "mobile"},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "user"}
    assert "expires_in" not in body
    assert _set_cookies(response) == []
    assert _rows(isolated_db) == []
    payload = auth.decode_access_token(body["access_token"])
    assert payload["typ"] == "access"
    assert payload["exp"] - payload["iat"] == JWT_EXPIRATION_MINUTES * 60

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    current = client.get("/users/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert current.status_code == 200
    assert current.json()["email"] == email


def test_register_modo_cookie_exige_csrf_e_emite_cookie(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    denied = client.post(
        "/auth/register",
        json={
            "display_name": "Usuario Web",
            "email": "registro.csrf@example.com",
            "password": PASSWORD,
            "session": "cookie",
            **_consent(),
        },
    )
    assert denied.status_code == 403
    assert _rows(isolated_db) == []
    assert _login(client, "registro.csrf@example.com").status_code == 401

    response = client.post(
        "/auth/register",
        json={
            "display_name": "Usuario Web",
            "email": "registro.csrf@example.com",
            "password": PASSWORD,
            "session": "cookie",
            **_consent(),
        },
        headers={**_csrf_headers(), "X-MAE-Client": "web"},
    )
    assert response.status_code == 200
    assert response.json()["expires_in"] == WEB_ACCESS_TOKEN_MINUTES * 60
    assert response.headers["cache-control"] == "no-store"
    raw = _assert_refresh_cookie(
        _set_cookies(response)[0],
        max_age=REFRESH_TOKEN_IDLE_DAYS * 24 * 60 * 60,
    )
    assert _rows(isolated_db)[0]["token_hash"] == _sha256(raw)


def test_refresh_valido_rotaciona(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "refresh.ok@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    first = _assert_refresh_cookie(
        _set_cookies(login)[0],
        max_age=REFRESH_TOKEN_IDLE_DAYS * 24 * 60 * 60,
    )

    refreshed = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": first},
    )
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert body["expires_in"] == WEB_ACCESS_TOKEN_MINUTES * 60
    assert refreshed.headers["cache-control"] == "no-store"
    second = _assert_refresh_cookie(
        _set_cookies(refreshed)[0],
        max_age=REFRESH_TOKEN_IDLE_DAYS * 24 * 60 * 60,
    )
    assert second != first
    assert second not in refreshed.text

    rows = _rows(isolated_db)
    by_hash = {row["token_hash"]: row for row in rows}
    assert by_hash[_sha256(first)]["used_at"]
    assert by_hash[_sha256(first)]["replaced_by_hash"] == _sha256(second)
    assert by_hash[_sha256(second)]["used_at"] is None
    assert by_hash[_sha256(second)]["family_id"] == by_hash[_sha256(first)]["family_id"]
    assert (
        by_hash[_sha256(second)]["family_expires_at"]
        == by_hash[_sha256(first)]["family_expires_at"]
    )

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200


def test_refresh_reutilizado_revoga_familia(isolated_db, monkeypatch, caplog):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "reuso@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    first = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)
    refreshed = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": first},
    )
    second = _assert_refresh_cookie(
        _set_cookies(refreshed)[0],
        max_age=REFRESH_TOKEN_IDLE_DAYS * 86400,
    )

    with caplog.at_level(logging.WARNING, logger="services.web_session"):
        reused = client.post(
            "/auth/refresh",
            headers=_csrf_headers(),
            cookies={"__Secure-mae_rt": first},
        )
    assert reused.status_code == 401
    assert reused.json()["detail"] == "Sessao invalida"
    _assert_cleared_cookie(_set_cookies(reused)[0])
    warnings = [
        record
        for record in caplog.records
        if "refresh_reuse_detected" in record.getMessage()
    ]
    assert warnings
    assert all(record.levelno == logging.WARNING for record in warnings)
    assert first not in caplog.text
    assert second not in caplog.text
    assert _sha256(first) not in caplog.text
    assert _sha256(second) not in caplog.text

    rows = _rows(isolated_db)
    assert rows
    assert all(row["revoked_at"] or row["used_at"] for row in rows)
    assert not any(row["used_at"] is None and row["revoked_at"] is None for row in rows)

    again = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": second},
    )
    assert again.status_code == 401
    assert again.json()["detail"] == "Sessao invalida"
    _assert_cleared_cookie(_set_cookies(again)[0])


def test_refresh_expirado_por_ociosidade(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "idle@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    raw = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)
    _update_token(isolated_db, _sha256(raw), expires_at="2000-01-01T00:00:00+00:00")

    response = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Sessao invalida"
    _assert_cleared_cookie(_set_cookies(response)[0])
    rows = _rows(isolated_db)
    assert rows
    assert all(row["revoked_at"] for row in rows)
    assert {row["revoke_reason"] for row in rows} == {"expired"}


def test_refresh_expirado_por_limite_absoluto(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "absoluto@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    raw = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)
    future = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0).isoformat()
    _update_token(
        isolated_db,
        _sha256(raw),
        expires_at=future,
        family_expires_at="2000-01-01T00:00:00+00:00",
    )

    response = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Sessao invalida"
    _assert_cleared_cookie(_set_cookies(response)[0])
    rows = _rows(isolated_db)
    assert all(row["revoke_reason"] == "expired" for row in rows)


def test_logout_idempotente_revoga_e_limpa_cookie(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "logout@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    raw = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)

    first = client.post(
        "/auth/logout",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert first.status_code == 204
    assert first.content == b""
    assert first.headers["cache-control"] == "no-store"
    _assert_cleared_cookie(_set_cookies(first)[0])
    rows = _rows(isolated_db)
    assert all(row["revoked_at"] and row["revoke_reason"] == "logout" for row in rows)

    second = client.post(
        "/auth/logout",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert second.status_code == 204
    _assert_cleared_cookie(_set_cookies(second)[0])

    missing = client.post("/auth/logout", headers=_csrf_headers())
    assert missing.status_code == 204
    _assert_cleared_cookie(_set_cookies(missing)[0])

    refreshed = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert refreshed.status_code == 401
    assert refreshed.json()["detail"] == "Sessao invalida"
    _assert_cleared_cookie(_set_cookies(refreshed)[0])


@pytest.mark.parametrize(
    ("path", "opt_in"),
    [
        ("/auth/login", True),
        ("/auth/register", True),
        ("/auth/refresh", False),
        ("/auth/logout", False),
    ],
)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-MAE-CSRF": "1"},
        {"Origin": ORIGIN},
        {"Origin": EVIL_ORIGIN, "X-MAE-CSRF": "1"},
        {"Origin": ORIGIN, "X-MAE-CSRF": "0"},
    ],
)
def test_csrf_origin_obrigatorio_no_modo_cookie(
    isolated_db,
    monkeypatch,
    path: str,
    opt_in: bool,
    headers: dict[str, str],
):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "csrf@example.com"
    if path != "/auth/register":
        _register(client, email)
    payload = None
    if path == "/auth/login":
        payload = {"email": email, "password": PASSWORD, "session": "cookie"}
    elif path == "/auth/register":
        payload = {
            "display_name": "Usuario Web",
            "email": email,
            "password": PASSWORD,
            "session": "cookie",
            **_consent(),
        }
    response = client.post(path, headers=headers, json=payload)
    assert response.status_code == 403
    if opt_in:
        assert response.headers["cache-control"] == "no-store"


def test_csrf_negado_nao_consome_sessao(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "csrf.vivo@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    raw = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)

    blocked = client.post("/auth/refresh", cookies={"__Secure-mae_rt": raw})
    assert blocked.status_code == 403
    logout_blocked = client.post(
        "/auth/logout",
        headers={"Origin": EVIL_ORIGIN, "X-MAE-CSRF": "1"},
        cookies={"__Secure-mae_rt": raw},
    )
    assert logout_blocked.status_code == 403

    refreshed = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert refreshed.status_code == 200


def test_cors_preflight_so_origem_permitida(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    allowed = client.options(
        "/auth/refresh",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-mae-csrf",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert allowed.headers["access-control-allow-methods"] == (
        "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    )
    assert "*" not in allowed.headers["access-control-allow-methods"]
    header_names = {
        item.strip().lower()
        for item in allowed.headers["access-control-allow-headers"].split(",")
    }
    assert {"authorization", "content-type", "x-mae-csrf"} <= header_names
    assert "*" not in allowed.headers["access-control-allow-headers"]
    assert "origin" in allowed.headers["vary"].lower()

    denied = client.options(
        "/auth/refresh",
        headers={
            "Origin": EVIL_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-mae-csrf",
        },
    )
    assert denied.status_code == 400
    assert denied.headers.get("access-control-allow-origin") != EVIL_ORIGIN

    foreign_header = client.options(
        "/auth/refresh",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-custom",
        },
    )
    assert foreign_header.status_code == 400

    health = client.get("/health", headers={"Origin": ORIGIN})
    assert health.status_code == 200
    assert health.headers["access-control-allow-origin"] == ORIGIN
    assert health.headers["access-control-allow-credentials"] == "true"
    assert "origin" in health.headers["vary"].lower()

    health_evil = client.get("/health", headers={"Origin": EVIL_ORIGIN})
    assert health_evil.status_code == 200
    assert health_evil.headers.get("access-control-allow-origin") != EVIL_ORIGIN


def test_cache_control_no_store_nas_rotas_de_auth(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    bad_login = _login(client, "nao.existe@example.com")
    assert bad_login.status_code == 401
    assert bad_login.headers["cache-control"] == "no-store"

    refresh = client.post("/auth/refresh", headers=_csrf_headers())
    assert refresh.status_code == 401
    assert refresh.headers["cache-control"] == "no-store"
    _assert_cleared_cookie(_set_cookies(refresh)[0])

    logout = client.post("/auth/logout", headers=_csrf_headers())
    assert logout.status_code == 204
    assert logout.headers["cache-control"] == "no-store"


def test_refresh_monta_access_do_banco_sem_estender_familia(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "banco@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    raw = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)
    token_hash = _sha256(raw)
    fixed_family = "2030-01-01T00:00:00+00:00"
    short_idle = "2026-12-01T00:00:00+00:00"
    _update_token(
        isolated_db,
        token_hash,
        expires_at=short_idle,
        family_expires_at=fixed_family,
    )
    with sqlite3.connect(isolated_db) as connection:
        connection.execute(
            """
            UPDATE users
            SET display_name = ?, email = ?
            WHERE email = ?
            """,
            ("Nome Novo", "outro@example.com", email),
        )

    response = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert response.status_code == 200
    payload = auth.decode_access_token(response.json()["access_token"])
    assert payload["display_name"] == "Nome Novo"
    assert payload["email"] == "outro@example.com"
    assert payload["typ"] == "access"

    rows = _rows(isolated_db)
    assert {row["family_expires_at"] for row in rows} == {fixed_family}
    successor = next(row for row in rows if row["used_at"] is None)
    assert successor["expires_at"] != short_idle
    assert successor["family_expires_at"] == fixed_family


def test_refresh_usuario_inativo(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "inativo@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    raw = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)
    with sqlite3.connect(isolated_db) as connection:
        connection.execute(
            "UPDATE users SET deleted_at = ? WHERE email = ?",
            ("2026-10-19T00:00:00+00:00", email),
        )

    response = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Sessao invalida"
    _assert_cleared_cookie(_set_cookies(response)[0])
    rows = _rows(isolated_db)
    assert rows
    assert all(row["revoke_reason"] == "user_inactive" for row in rows)
    assert all(row["revoked_at"] for row in rows)


def test_termos_desatualizados_nao_bloqueiam_refresh(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "termos@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    raw = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)
    with sqlite3.connect(isolated_db) as connection:
        connection.execute(
            "UPDATE users SET terms_version = ? WHERE email = ?",
            ("0.0", email),
        )

    response = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert response.status_code == 200
    blocked = client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {response.json()['access_token']}"},
    )
    assert blocked.status_code == 403


def test_reset_de_senha_revoga_familias(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "reset.familias@example.com"
    _register(client, email)
    first_login = _login(client, email, headers=_csrf_headers(), session="cookie")
    second_login = _login(client, email, headers=_csrf_headers(), session="cookie")
    first = _assert_refresh_cookie(
        _set_cookies(first_login)[0],
        max_age=REFRESH_TOKEN_IDLE_DAYS * 86400,
    )
    second = _assert_refresh_cookie(
        _set_cookies(second_login)[0],
        max_age=REFRESH_TOKEN_IDLE_DAYS * 86400,
    )
    assert {row["family_id"] for row in _rows(isolated_db)}.__len__() == 2

    requested = client.post("/auth/password-reset/request", json={"email": email})
    assert requested.status_code == 200
    confirmed = client.post(
        "/auth/password-reset/confirm",
        json={"token": requested.json()["reset_token"], "new_password": "nova-senha-123"},
    )
    assert confirmed.status_code == 200
    rows = _rows(isolated_db)
    assert len(rows) == 2
    assert all(row["revoked_at"] and row["revoke_reason"] == "password_reset" for row in rows)

    for raw in (first, second):
        response = client.post(
            "/auth/refresh",
            headers=_csrf_headers(),
            cookies={"__Secure-mae_rt": raw},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Sessao invalida"
        _assert_cleared_cookie(_set_cookies(response)[0])


def test_delete_usuario_revoga_familias(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "delete.familias@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    raw = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)
    access = login.json()["access_token"]

    deleted = client.request(
        "DELETE",
        "/users/me",
        headers={"Authorization": f"Bearer {access}"},
        json={"confirm": "DELETE"},
    )
    assert deleted.status_code == 200
    rows = _rows(isolated_db)
    assert rows
    assert all(row["revoked_at"] and row["revoke_reason"] == "account_deleted" for row in rows)

    response = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Sessao invalida"
    _assert_cleared_cookie(_set_cookies(response)[0])


@pytest.mark.parametrize(
    "case",
    [
        "invalid.token.x",
        "duas_partes",
        "pontos",
        "vazio",
        "nao_ascii",
        "parte_nao_ascii",
        "alg_none",
        "assinatura_trocada",
        "expirado",
        "payload_nao_json",
        "sem_exp",
        "typ_refresh",
        "opaco",
        "snowman",
    ],
)
@pytest.mark.parametrize("path", ["/auth/me", "/users/me"])
def test_bearer_malformado_sempre_401(isolated_db, case: str, path: str):
    token = _malformed_token(case)
    try:
        token.encode("ascii")
    except UnicodeEncodeError:
        assert _asgi_status(_load_app(), path, token) == 401
        return

    response = _client().get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def _legacy_access_token(
    *,
    user_id: str,
    email: str,
    display_name: str,
    exp: int | None = None,
    alg: str = "HS256",
    typ: str | None = None,
    include_typ: bool = False,
) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "sub": user_id,
        "user_id": user_id,
        "email": email,
        "display_name": display_name,
        "iat": now - 60,
        "exp": now + 3600 if exp is None else exp,
    }
    if include_typ:
        payload["typ"] = typ
    if alg == "HS256":
        return auth._encode_jwt(payload)
    return _signed_token({"alg": alg, "typ": "JWT"}, auth._encode_segment(payload))


@pytest.mark.parametrize("path", ["/auth/me", "/users/me"])
def test_jwt_legado_sem_typ_valido_da_200(isolated_db, path: str):
    client = _client()
    email = "legado.valido@example.com"
    registered = _register(client, email)
    assert registered.status_code == 200
    user = registered.json()["user"]
    token = _legacy_access_token(
        user_id=user["user_id"],
        email=user["email"],
        display_name=user["display_name"],
    )
    payload_segment = token.split(".")[1]
    payload = json.loads(auth._urlsafe_b64decode(payload_segment))
    assert "typ" not in payload

    response = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/auth/me", "/users/me"])
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("typ_diferente", 401),
        ("vencido", 401),
        ("alg_diferente", 401),
    ],
)
def test_jwt_legado_invalido_da_401(isolated_db, path: str, kind: str, expected: int):
    client = _client()
    email = "legado.invalido@example.com"
    registered = _register(client, email)
    assert registered.status_code == 200
    user = registered.json()["user"]
    now = int(time.time())
    if kind == "typ_diferente":
        token = _legacy_access_token(
            user_id=user["user_id"],
            email=user["email"],
            display_name=user["display_name"],
            include_typ=True,
            typ="refresh",
        )
    elif kind == "vencido":
        token = _legacy_access_token(
            user_id=user["user_id"],
            email=user["email"],
            display_name=user["display_name"],
            exp=now - 60,
        )
    else:
        token = _legacy_access_token(
            user_id=user["user_id"],
            email=user["email"],
            display_name=user["display_name"],
            alg="HS384",
        )

    response = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == expected


def test_refresh_opaco_nao_serve_como_bearer(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "opaco.bearer@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    raw = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {raw}"})
    users = client.get("/users/me", headers={"Authorization": f"Bearer {raw}"})
    assert me.status_code == 401
    assert users.status_code == 401

    refreshed = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert refreshed.status_code == 200


def test_jwt_nao_serve_como_refresh(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "jwt.refresh@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    raw = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)
    access = login.json()["access_token"]

    rejected = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": access},
    )
    assert rejected.status_code == 401
    assert rejected.json()["detail"] == "Sessao invalida"
    _assert_cleared_cookie(_set_cookies(rejected)[0])

    still_valid = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": raw},
    )
    assert still_valid.status_code == 200


def test_rotacao_concorrente_so_uma_200(isolated_db, monkeypatch):
    _allow_web_origin(monkeypatch)
    client = _client()
    email = "concorrente@example.com"
    _register(client, email)
    login = _login(client, email, headers=_csrf_headers(), session="cookie")
    raw = _assert_refresh_cookie(_set_cookies(login)[0], max_age=REFRESH_TOKEN_IDLE_DAYS * 86400)
    barrier = threading.Barrier(2)
    real_connect = sqlite3.connect

    class _ConnectionProxy:
        def __init__(self, inner: sqlite3.Connection):
            object.__setattr__(self, "_inner", inner)

        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str) and _is_rotation_update(sql):
                barrier.wait(timeout=10)
            return self._inner.execute(sql, *args, **kwargs)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            return self._inner.__exit__(exc_type, exc, tb)

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __setattr__(self, name, value):
            setattr(self._inner, name, value)

    def _connect(*args, **kwargs):
        return _ConnectionProxy(real_connect(*args, **kwargs))

    monkeypatch.setattr("database.repository.sqlite3.connect", _connect)
    app = _load_app()

    def _call():
        from fastapi.testclient import TestClient

        local = TestClient(app)
        return local.post(
            "/auth/refresh",
            headers=_csrf_headers(),
            cookies={"__Secure-mae_rt": raw},
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=20) for future in (pool.submit(_call), pool.submit(_call))]
    finally:
        monkeypatch.setattr("database.repository.sqlite3.connect", real_connect)

    codes = sorted(response.status_code for response in results)
    assert codes == [200, 401]
    loser = next(response for response in results if response.status_code == 401)
    winner = next(response for response in results if response.status_code == 200)
    assert loser.json()["detail"] == "Sessao invalida"
    _assert_cleared_cookie(_set_cookies(loser)[0])
    winner_cookie = _assert_refresh_cookie(
        _set_cookies(winner)[0],
        max_age=REFRESH_TOKEN_IDLE_DAYS * 86400,
    )

    rows = _rows(isolated_db)
    assert not any(row["used_at"] is None and row["revoked_at"] is None for row in rows)
    follow = client.post(
        "/auth/refresh",
        headers=_csrf_headers(),
        cookies={"__Secure-mae_rt": winner_cookie},
    )
    assert follow.status_code == 401


def test_indice_family_expires_at_sqlite(isolated_db):
    connection = sqlite3.connect(isolated_db)
    try:
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'refresh_tokens'"
            )
        }
        assert "idx_refresh_tokens_family_expires_at" in indexes
        info = connection.execute(
            "PRAGMA index_info(idx_refresh_tokens_family_expires_at)"
        ).fetchall()
        assert [row[2] for row in info] == ["family_expires_at"]
        columns = [row[1] for row in connection.execute("PRAGMA table_info(refresh_tokens)")]
        assert "token_hash" in columns
        assert "ip" not in columns
        assert "user_agent" not in columns
        migration = connection.execute(
            "SELECT name FROM schema_migrations WHERE version = 6"
        ).fetchone()
        assert migration[0] == "refresh_token_families"
    finally:
        connection.close()


def test_indice_ttl_family_expires_at_mongo():
    from database import mongo_repository

    class _Column:
        def __init__(self):
            self.calls = []

        def create_index(self, keys, **kwargs):
            self.calls.append((keys, kwargs))

    class _Database:
        def __init__(self):
            self.columns = {}

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            self.columns.setdefault(name, _Column())
            return self.columns[name]

    previous = mongo_repository._indexes_ready
    mongo_repository._indexes_ready = False
    database = _Database()
    try:
        mongo_repository._ensure_indexes(database)
    finally:
        mongo_repository._indexes_ready = previous

    specs = database.columns["refresh_tokens"].calls
    ttl = [item for item in specs if item[0] == "family_expires_at"]
    assert len(ttl) == 1
    assert ttl[0][1]["expireAfterSeconds"] == mongo_repository.REFRESH_FAMILY_TTL_GRACE_SECONDS
    assert ttl[0][1]["expireAfterSeconds"] > 0
    assert not any(
        item[0] == "expires_at" and "expireAfterSeconds" in item[1]
        for item in specs
    )


def test_mongo_rotacao_usa_find_one_and_update_condicional():
    from datetime import datetime

    from pymongo import ReturnDocument

    from database import mongo_repository

    family_expires = datetime(2030, 1, 1, tzinfo=UTC)
    now = datetime(2026, 10, 19, tzinfo=UTC)
    existing = {
        "token_hash": "apresentado",
        "family_id": "familia-1",
        "user_id": "user_1",
        "family_expires_at": family_expires,
        "expires_at": datetime(2026, 10, 26, tzinfo=UTC),
        "used_at": None,
        "revoked_at": None,
    }

    class _Collection:
        def __init__(self):
            self.calls = []
            self.inserted = None

        def find_one_and_update(self, filt, update, return_document=None):
            self.calls.append((filt, update, return_document))
            return dict(existing)

        def insert_one(self, document):
            self.inserted = document

    collection = _Collection()
    monkey_target = "database.mongo_repository._get_collection"

    def _collection(name):
        assert name == "refresh_tokens"
        return collection

    original = mongo_repository._get_collection
    mongo_repository._get_collection = _collection
    try:
        rotated = mongo_repository.rotate_refresh_token(
            presented_hash="apresentado",
            new_hash="sucessor",
            now_iso=now.isoformat(),
            new_expires_at="2026-10-26T00:00:00+00:00",
            created_at=now.isoformat(),
        )
    finally:
        mongo_repository._get_collection = original

    filt, update, return_document = collection.calls[0]
    assert filt["token_hash"] == "apresentado"
    assert filt["used_at"] is None
    assert filt["revoked_at"] is None
    assert filt["expires_at"]["$gt"] == now
    assert filt["family_expires_at"]["$gt"] == now
    assert update == {"$set": {"used_at": now, "replaced_by_hash": "sucessor"}}
    assert return_document == ReturnDocument.BEFORE
    assert collection.inserted["family_expires_at"] is family_expires
    assert collection.inserted["family_id"] == "familia-1"
    assert collection.inserted["expires_at"].isoformat() == "2026-10-26T00:00:00+00:00"
    assert rotated["family_expires_at"] == family_expires.isoformat()
    assert rotated["family_id"] == "familia-1"


def _asgi_status(app, path: str, token: str) -> int:
    try:
        token_bytes = token.encode("latin-1")
    except UnicodeEncodeError:
        token_bytes = token.encode("utf-8")
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer " + token_bytes)],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
        "root_path": "",
    }

    async def _run():
        await app(scope, receive, send)

    anyio.run(_run)
    return next(
        message["status"]
        for message in messages
        if message["type"] == "http.response.start"
    )


def _update_token(db_path, token_hash: str, **fields: str) -> None:
    assignments = ", ".join(f"{column} = ?" for column in fields)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE refresh_tokens SET {assignments} WHERE token_hash = ?",
            (*fields.values(), token_hash),
        )


def _is_rotation_update(sql: str) -> bool:
    compact = " ".join(sql.split())
    return (
        compact.startswith("UPDATE refresh_tokens")
        and "SET used_at" in compact
        and "used_at IS NULL" in compact
        and "revoked_at IS NULL" in compact
    )
