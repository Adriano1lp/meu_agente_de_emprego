from __future__ import annotations

import sqlite3
import sys
from types import ModuleType
from unittest.mock import MagicMock

from config import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION
from database.repository import (
    create_generated_file,
    create_processing_run,
    create_user_document,
    get_user_by_email,
    get_user_by_id,
    list_consent_log,
    update_user_consent,
    upsert_user_profile,
)


def _install_heavy_service_stubs() -> None:
    stubs = {
        "services.main_chat": {
            "generate_cover_letter": MagicMock(),
            "pipeline_with_details": MagicMock(),
        },
        "services.main_carta": {
            "gerar_pdf_carta_apresentacao": MagicMock(),
        },
        "services.main_curriculo": {
            "gerar_pdf_profissional": MagicMock(),
        },
        "services.main_rag": {
            "rebuild_vectorstore_for_user": MagicMock(),
        },
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


def _client():
    _install_heavy_service_stubs()
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)


def _register(client, email: str, display_name: str = "Usuario LGPD") -> dict:
    response = client.post(
        "/auth/register",
        json={
            "display_name": display_name,
            "email": email,
            "password": "senha-forte-123",
            "terms_accepted": True,
            "terms_version": CURRENT_TERMS_VERSION,
            "privacy_accepted": True,
            "privacy_version": CURRENT_PRIVACY_VERSION,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "token": body["access_token"],
        "user_id": body["user"]["user_id"],
        "email": email,
        "auth": {"Authorization": f"Bearer {body['access_token']}"},
    }


def _collect_keys(value) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key).lower())
            keys.update(_collect_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_collect_keys(item))
    return keys


def test_export_requires_auth(isolated_db):
    client = _client()
    response = client.get("/users/me/export")
    assert response.status_code == 401


def test_export_returns_profile_history_and_file_metadata(isolated_db):
    client = _client()
    session = _register(client, "export.lgpd@example.com", display_name="Ada Export")
    user_id = session["user_id"]

    upsert_user_profile(
        user_id,
        1,
        {"nome_completo": "Ada Export", "email": "export.lgpd@example.com"},
    )
    create_user_document(
        {
            "user_id": user_id,
            "document_type": "cv",
            "original_filename": "curriculo.pdf",
            "original_content_type": "application/pdf",
            "original_file_path": "/tmp/curriculo.pdf",
            "extracted_text_path": "/tmp/cv.txt",
            "extracted_text": "texto do cv",
            "bytes_received": 128,
            "checksum_sha256": "abc",
        },
    )
    processing_run_id = create_processing_run(
        {
            "user_id": user_id,
            "input_text": "Vaga de engenheira de software",
            "job_data": {"empresa": "Acme"},
            "matching": None,
            "optimization": None,
            "response_text": "analise",
            "status": "completed",
            "error_message": None,
            "completed_at": "2026-09-05T12:00:00+00:00",
        },
    )
    create_generated_file(
        {
            "user_id": user_id,
            "processing_run_id": processing_run_id,
            "file_name": "curriculo-otimizado.pdf",
            "file_path": "/tmp/curriculo-otimizado.pdf",
            "public_url": "http://localhost/users/me/files/curriculo-otimizado.pdf",
            "media_type": "application/pdf",
            "bytes_size": 2048,
        },
    )

    response = client.get("/users/me/export", headers=session["auth"])
    assert response.status_code == 200
    body = response.json()

    assert body["user"]["email"] == "export.lgpd@example.com"
    assert body["user"]["display_name"] == "Ada Export"
    assert body["user"]["terms_version"] == CURRENT_TERMS_VERSION
    assert body["user"]["privacy_version"] == CURRENT_PRIVACY_VERSION
    assert body["profile"]["profile"]["nome_completo"] == "Ada Export"
    assert body["processing_runs"][0]["input_text"] == "Vaga de engenheira de software"
    assert body["documents"][0]["original_filename"] == "curriculo.pdf"
    assert body["documents"][0]["original_content_type"] == "application/pdf"
    assert "created_at" in body["documents"][0]
    assert body["generated_files"][0]["file_name"] == "curriculo-otimizado.pdf"
    assert body["generated_files"][0]["media_type"] == "application/pdf"
    assert "exported_at" in body

    keys = _collect_keys(body)
    assert "password_hash" not in keys
    assert "password" not in keys
    assert "access_token" not in keys
    assert "jwt" not in keys


def test_export_blocks_outdated_consent(isolated_db):
    client = _client()
    session = _register(client, "outdated.export@example.com")
    update_user_consent(
        session["user_id"],
        doc="terms",
        version="0.9",
        accepted_at="2020-01-01T00:00:00+00:00",
    )
    response = client.get("/users/me/export", headers=session["auth"])
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "TERMS_OUTDATED"


def test_delete_requires_auth(isolated_db):
    client = _client()
    response = client.delete("/users/me", json={"confirm": "DELETE"})
    assert response.status_code == 401


def test_delete_requires_exact_confirm(isolated_db):
    client = _client()
    session = _register(client, "confirm.lgpd@example.com")

    missing = client.delete("/users/me", headers=session["auth"])
    assert missing.status_code == 400

    empty = client.delete("/users/me", headers=session["auth"], json={})
    assert empty.status_code == 400

    wrong = client.delete(
        "/users/me",
        headers=session["auth"],
        json={"confirm": "delete"},
    )
    assert wrong.status_code == 400
    assert get_user_by_id(session["user_id"]) is not None


def test_delete_scrubs_user_keeps_consent_and_blocks_login(isolated_db):
    client = _client()
    session = _register(client, "delete.lgpd@example.com")
    user_id = session["user_id"]
    consent_before = list_consent_log(user_id)
    assert len(consent_before) == 2

    import config

    user_dir = config.USERS_DIR / user_id
    sample_file = user_dir / "documents" / "cv.txt"
    sample_file.parent.mkdir(parents=True, exist_ok=True)
    sample_file.write_text("texto sensivel do curriculo", encoding="utf-8")
    chroma_file = user_dir / "chroma" / "index.bin"
    chroma_file.parent.mkdir(parents=True, exist_ok=True)
    chroma_file.write_bytes(b"embedding")

    upsert_user_profile(user_id, 1, {"nome_completo": "PII"})
    create_processing_run(
        {
            "user_id": user_id,
            "input_text": "historico com PII",
            "job_data": None,
            "matching": None,
            "optimization": None,
            "response_text": None,
            "status": "completed",
            "error_message": None,
            "completed_at": "2026-09-05T12:00:00+00:00",
        },
    )

    response = client.delete(
        "/users/me",
        headers=session["auth"],
        json={"confirm": "DELETE"},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["deleted_at"]

    assert get_user_by_id(user_id) is None
    assert get_user_by_email("delete.lgpd@example.com") is None
    assert not user_dir.exists()

    consent_after = list_consent_log(user_id)
    assert len(consent_after) == 2
    assert [(row["doc"], row["version"]) for row in consent_after] == [
        (row["doc"], row["version"]) for row in consent_before
    ]
    assert all(row["accepted_at"] for row in consent_after)
    assert all(row["user_id"] == user_id for row in consent_after)

    with sqlite3.connect(isolated_db) as connection:
        row = connection.execute(
            """
            SELECT email, display_name, password_hash, deleted_at
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        processing_count = connection.execute(
            "SELECT COUNT(*) FROM processing_runs WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        consent_count = connection.execute(
            "SELECT COUNT(*) FROM consent_log WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'consent_log'",
        ).fetchone()[0]

    assert row is not None
    assert row[0] == f"deleted+{user_id}@invalid.local"
    assert row[1] == "Conta excluida"
    assert row[2] == "deleted_account"
    assert row[3]
    assert processing_count == 0
    assert consent_count == 2
    assert "CASCADE" not in table_sql.upper()

    login = client.post(
        "/auth/login",
        json={"email": "delete.lgpd@example.com", "password": "senha-forte-123"},
    )
    assert login.status_code == 401

    me = client.get("/auth/me", headers=session["auth"])
    assert me.status_code == 401

    export = client.get("/users/me/export", headers=session["auth"])
    assert export.status_code == 401

    reused = _register(client, "delete.lgpd@example.com", display_name="Conta Nova")
    assert reused["user_id"] != user_id
