from __future__ import annotations

from config import get_user_base_dir
from database.repository import get_user_by_id


def _register(client, email: str) -> dict[str, str]:
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Usuario LGPD",
            "email": email,
            "password": "senha-forte-123",
            "terms_accepted": True,
            "privacy_accepted": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    return {
        "token": body["access_token"],
        "user_id": body["user"]["user_id"],
        "auth": {"Authorization": f"Bearer {body['access_token']}"},
    }


def test_export_requires_auth(client) -> None:
    response = client.get("/users/me/export")
    assert response.status_code == 401


def test_export_returns_user_json_without_password(client) -> None:
    session = _register(client, "export.lgpd@example.com")
    profile = client.post(
        "/users/me/profile",
        headers=session["auth"],
        json={"nome_completo": "Usuario LGPD", "email": "export.lgpd@example.com"},
    )
    assert profile.status_code == 200

    response = client.get("/users/me/export", headers=session["auth"])
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "export.lgpd@example.com"
    assert "password_hash" not in body["user"]
    assert body["profile"]["profile"]["nome_completo"] == "Usuario LGPD"
    assert isinstance(body["consent_log"], list)
    assert len(body["consent_log"]) == 2
    assert "exported_at" in body


def test_delete_account_anonymizes_and_purges_files(client) -> None:
    session = _register(client, "delete.lgpd@example.com")
    user_dir = get_user_base_dir(session["user_id"])
    sample_file = user_dir / "documents" / "cv.txt"
    sample_file.parent.mkdir(parents=True, exist_ok=True)
    sample_file.write_text("texto sensivel do curriculo", encoding="utf-8")

    response = client.delete("/users/me", headers=session["auth"])
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["deleted_at"]

    assert get_user_by_id(session["user_id"]) is None
    assert not user_dir.exists()

    me = client.get("/auth/me", headers=session["auth"])
    assert me.status_code == 404

    login = client.post(
        "/auth/login",
        json={"email": "delete.lgpd@example.com", "password": "senha-forte-123"},
    )
    assert login.status_code == 401

    second_delete = client.delete("/users/me", headers=session["auth"])
    assert second_delete.status_code == 404


def test_delete_requires_auth(client) -> None:
    response = client.delete("/users/me")
    assert response.status_code == 401
