from __future__ import annotations

from database.repository import list_consent_log


def test_legal_endpoint_returns_current_versions(client) -> None:
    response = client.get("/legal")
    assert response.status_code == 200
    body = response.json()
    assert body["terms_of_service"]["id"] == "terms_of_service"
    assert body["terms_of_service"]["version"] == "tos_v1"
    assert body["privacy_policy"]["id"] == "privacy_policy"
    assert body["privacy_policy"]["version"] == "privacy_v1"


def test_register_requires_terms_acceptance(client) -> None:
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Sem Termo",
            "email": "sem.termo@example.com",
            "password": "senha-forte-123",
            "terms_accepted": False,
            "privacy_accepted": True,
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Aceite do termo de uso obrigatorio"


def test_register_requires_privacy_acceptance(client) -> None:
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Sem Privacidade",
            "email": "sem.privacidade@example.com",
            "password": "senha-forte-123",
            "terms_accepted": True,
            "privacy_accepted": False,
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Aceite da politica de privacidade obrigatorio"


def test_register_stores_versions_and_consent_log(client, register_payload) -> None:
    response = client.post("/auth/register", json=register_payload)
    assert response.status_code == 200
    user = response.json()["user"]
    assert user["terms_accepted"] is True
    assert user["privacy_accepted"] is True
    assert user["terms_version"] == "tos_v1"
    assert user["privacy_version"] == "privacy_v1"
    assert user["terms_accepted_at"]
    assert user["privacy_accepted_at"]
    assert user["needs_reconsent"] is False

    entries = list_consent_log(user["user_id"])
    assert len(entries) == 2
    document_types = {entry["document_type"] for entry in entries}
    assert document_types == {"terms_of_service", "privacy_policy"}
    assert all(entry["source"] == "register" for entry in entries)
    assert all(entry["accepted"] is True for entry in entries)
    assert all(entry["document_version"] in {"tos_v1", "privacy_v1"} for entry in entries)


def test_accept_terms_upgrades_existing_user(client) -> None:
    register = client.post(
        "/auth/register",
        json={
            "display_name": "Usuario Existente",
            "email": "existente.legal@example.com",
            "password": "senha-forte-123",
            "terms_accepted": True,
            "privacy_accepted": True,
        },
    )
    token = register.json()["access_token"]
    user_id = register.json()["user"]["user_id"]

    response = client.post(
        "/users/me/terms/accept",
        headers={"Authorization": f"Bearer {token}"},
        json={"accepted": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["terms_version"] == "tos_v1"
    assert body["privacy_version"] == "privacy_v1"
    assert body["privacy_accepted"] is True

    entries = list_consent_log(user_id)
    sources = [entry["source"] for entry in entries]
    assert sources.count("register") == 2
    assert sources.count("accept") == 2


def test_accept_terms_rejects_explicit_privacy_refusal(client) -> None:
    register = client.post(
        "/auth/register",
        json={
            "display_name": "Recusa Privacidade",
            "email": "recusa.privacidade@example.com",
            "password": "senha-forte-123",
            "terms_accepted": True,
            "privacy_accepted": True,
        },
    )
    token = register.json()["access_token"]
    response = client.post(
        "/users/me/terms/accept",
        headers={"Authorization": f"Bearer {token}"},
        json={"accepted": True, "privacy_accepted": False},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Aceite da politica de privacidade obrigatorio"
