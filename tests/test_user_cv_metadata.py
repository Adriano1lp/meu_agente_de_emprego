from __future__ import annotations

import importlib.util
import re
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

from config import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION, get_user_chroma_dir
from database.repository import create_embedding_run, update_user_consent

CV_TEXT = "Curriculo de teste com experiencia em Python e analise de dados."
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
CV_FIELDS = {
    "filename",
    "content_type",
    "bytes",
    "uploaded_at",
    "embedding_status",
    "processed_at",
}
SECRET_CHROMA = "/interno/chroma/nao-expor"
SECRET_CV_PATH = "/interno/arquivos/cv-nao-expor.txt"
SECRET_OBJECT_KEY = "users/outro-tenant/documents/cv_original.pdf"
SECRET_ORIGINAL = "/interno/uploads/cv_original.pdf"
SECRET_ERROR = "traceback-interno-do-embedding"


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


def _load_real_save_user_cv():
    module_name = "real_user_data_for_cv_metadata_tests"
    if module_name in sys.modules:
        return sys.modules[module_name].save_user_cv

    path = Path(__file__).resolve().parents[1] / "services" / "user_data.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.save_user_cv


def _client():
    _install_heavy_service_stubs()
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)


def _register(client, email: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Usuario CV",
            "email": email,
            "password": "senha-forte-123",
            "terms_accepted": True,
            "terms_version": CURRENT_TERMS_VERSION,
            "privacy_accepted": True,
            "privacy_version": CURRENT_PRIVACY_VERSION,
        },
    )
    assert response.status_code == 200
    body = response.json()
    return {
        "user_id": body["user"]["user_id"],
        "auth": {"Authorization": f"Bearer {body['access_token']}"},
    }


def _save_cv_with_internal_fields(monkeypatch):
    real_save = _load_real_save_user_cv()

    def save(upload_file, user_id: str) -> dict:
        saved = real_save(upload_file, user_id)
        saved["cv_file"] = SECRET_CV_PATH
        saved["original_file"] = SECRET_ORIGINAL
        saved["object_key"] = SECRET_OBJECT_KEY
        saved["extracted_text_object_key"] = SECRET_OBJECT_KEY
        return saved

    monkeypatch.setattr("main.save_user_cv", save)


def _upload(client, session, filename: str = "curriculo.txt"):
    return client.post(
        "/users/me/upload-cv",
        headers=session["auth"],
        files={"file": (filename, CV_TEXT.encode("utf-8"), "text/plain")},
    )


def _mark_embeddings(user_id: str) -> None:
    chroma_dir = get_user_chroma_dir(user_id)
    (chroma_dir / "index").write_text("embeddings", encoding="utf-8")


def _record_embedding_run(
    user_id: str,
    document_id: int,
    *,
    status: str,
    processed_at: str,
) -> None:
    create_embedding_run(
        {
            "user_id": user_id,
            "document_id": document_id,
            "embedding_model": "text-embedding-3-small",
            "chunks": 2,
            "chroma_dir": SECRET_CHROMA,
            "cv_file_path": SECRET_CV_PATH,
            "status": status,
            "error_message": SECRET_ERROR,
            "processed_at": processed_at,
        },
    )


def _assert_cv_contract(body: dict) -> None:
    assert set(body) == CV_FIELDS
    assert isinstance(body["filename"], str) and body["filename"]
    assert isinstance(body["content_type"], str) and body["content_type"]
    assert type(body["bytes"]) is int
    assert body["bytes"] > 0
    assert isinstance(body["uploaded_at"], str)
    assert ISO_UTC.fullmatch(body["uploaded_at"])
    assert body["embedding_status"] in {"completed", "failed", "pending", "none"}
    if body["embedding_status"] == "none":
        assert body["processed_at"] is None
    else:
        assert isinstance(body["processed_at"], str)
        assert ISO_UTC.fullmatch(body["processed_at"])
    assert SECRET_CHROMA not in str(body)
    assert SECRET_CV_PATH not in str(body)
    assert SECRET_OBJECT_KEY not in str(body)
    assert SECRET_ERROR not in str(body)


def _assert_upload_hides_internal_fields(body: dict) -> None:
    assert "cv_file" not in body
    assert "original_file" not in body
    assert "object_key" not in body
    assert "extracted_text_object_key" not in body
    assert SECRET_CV_PATH not in str(body)
    assert SECRET_ORIGINAL not in str(body)
    assert SECRET_OBJECT_KEY not in str(body)
    embeddings = body.get("embeddings")
    if isinstance(embeddings, dict):
        assert "chroma_dir" not in embeddings
        assert "vector_store" not in embeddings
        assert "cv_file" not in embeddings
        assert SECRET_CHROMA not in str(embeddings)


def test_cv_requires_token(isolated_db):
    client = _client()
    response = client.get("/users/me/cv")
    assert response.status_code == 401


def test_cv_rejects_invalid_token(isolated_db):
    client = _client()
    response = client.get(
        "/users/me/cv",
        headers={"Authorization": "Bearer token-invalido"},
    )
    assert response.status_code == 401


def test_cv_not_found_without_document(isolated_db):
    client = _client()
    session = _register(client, "cv.vazio@example.com")
    response = client.get("/users/me/cv", headers=session["auth"])
    assert response.status_code == 404
    assert response.json()["detail"] == "Curriculo nao encontrado"


def test_cv_terms_outdated_is_403(isolated_db):
    client = _client()
    session = _register(client, "cv.termos@example.com")
    update_user_consent(
        session["user_id"],
        doc="terms",
        version="0.9",
        accepted_at="2020-01-01T00:00:00+00:00",
    )
    response = client.get("/users/me/cv", headers=session["auth"])
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "TERMS_OUTDATED"


def test_cv_metadata_none_then_completed_then_failed(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "cv.status@example.com")
    _save_cv_with_internal_fields(monkeypatch)
    monkeypatch.setattr(
        "main.rebuild_vectorstore_for_user",
        lambda user_id: _mark_embeddings(user_id) or {"chunks": 1},
    )
    uploaded = _upload(client, session, "curriculo.txt")
    assert uploaded.status_code == 200
    document_id = uploaded.json()["document_id"]

    missing = client.get("/users/me/cv", headers=session["auth"])
    assert missing.status_code == 200
    body = missing.json()
    _assert_cv_contract(body)
    assert body["filename"] == "curriculo.txt"
    assert body["content_type"] == "text/plain"
    assert body["bytes"] == len(CV_TEXT.encode("utf-8"))
    assert body["embedding_status"] == "none"
    assert body["processed_at"] is None
    assert session["user_id"] not in missing.text

    _record_embedding_run(
        session["user_id"],
        document_id,
        status="completed",
        processed_at="2026-01-02 03:04:05",
    )
    completed = client.get("/users/me/cv", headers=session["auth"]).json()
    _assert_cv_contract(completed)
    assert completed["embedding_status"] == "completed"
    assert completed["processed_at"] == "2026-01-02T03:04:05+00:00"

    _record_embedding_run(
        session["user_id"],
        document_id,
        status="failed",
        processed_at="2026-02-03 04:05:06",
    )
    failed = client.get("/users/me/cv", headers=session["auth"])
    failed_body = failed.json()
    _assert_cv_contract(failed_body)
    assert failed_body["embedding_status"] == "failed"
    assert failed_body["processed_at"] == "2026-02-03T04:05:06+00:00"
    assert SECRET_ERROR not in failed.text
    assert SECRET_CHROMA not in failed.text


def test_cv_pending_stays_pending(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "cv.pending@example.com")
    _save_cv_with_internal_fields(monkeypatch)
    monkeypatch.setattr("main.rebuild_vectorstore_for_user", lambda user_id: {"chunks": 0})
    uploaded = _upload(client, session)
    assert uploaded.status_code == 200

    _record_embedding_run(
        session["user_id"],
        uploaded.json()["document_id"],
        status="pending",
        processed_at="2026-03-04 05:06:07",
    )
    body = client.get("/users/me/cv", headers=session["auth"]).json()
    _assert_cv_contract(body)
    assert body["embedding_status"] == "pending"
    assert body["processed_at"] == "2026-03-04T05:06:07+00:00"


def test_cv_uses_latest_document_embedding_only(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "cv.recente@example.com")
    _save_cv_with_internal_fields(monkeypatch)
    monkeypatch.setattr("main.rebuild_vectorstore_for_user", lambda user_id: {"chunks": 0})

    first = _upload(client, session, "antigo.txt")
    assert first.status_code == 200
    _record_embedding_run(
        session["user_id"],
        first.json()["document_id"],
        status="completed",
        processed_at="2026-04-01 00:00:00",
    )

    second = _upload(client, session, "recente.txt")
    assert second.status_code == 200
    body = client.get("/users/me/cv", headers=session["auth"]).json()
    _assert_cv_contract(body)
    assert body["filename"] == "recente.txt"
    assert body["embedding_status"] == "none"
    assert body["processed_at"] is None
    assert "antigo.txt" not in str(body)


def test_cv_isolates_users(isolated_db, monkeypatch):
    client = _client()
    owner = _register(client, "cv.dono@example.com")
    other = _register(client, "cv.vizinho@example.com")
    _save_cv_with_internal_fields(monkeypatch)
    monkeypatch.setattr("main.rebuild_vectorstore_for_user", lambda user_id: {"chunks": 0})

    owner_upload = _upload(client, owner, "curriculo-dono.txt")
    other_upload = _upload(client, other, "curriculo-vizinho.txt")
    assert owner_upload.status_code == 200
    assert other_upload.status_code == 200
    _record_embedding_run(
        owner["user_id"],
        owner_upload.json()["document_id"],
        status="completed",
        processed_at="2026-05-01 08:00:00",
    )
    _record_embedding_run(
        other["user_id"],
        owner_upload.json()["document_id"],
        status="failed",
        processed_at="2026-05-02 08:00:00",
    )

    owner_body = client.get("/users/me/cv", headers=owner["auth"]).json()
    other_body = client.get("/users/me/cv", headers=other["auth"]).json()
    _assert_cv_contract(owner_body)
    _assert_cv_contract(other_body)
    assert owner_body["filename"] == "curriculo-dono.txt"
    assert owner_body["embedding_status"] == "completed"
    assert other_body["filename"] == "curriculo-vizinho.txt"
    assert other_body["embedding_status"] == "none"
    assert owner["user_id"] not in str(other_body)
    assert other["user_id"] not in str(owner_body)
    assert "curriculo-vizinho.txt" not in str(owner_body)
    assert "curriculo-dono.txt" not in str(other_body)


def test_upload_cv_hides_internal_paths_on_success_and_failure(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "cv.upload@example.com")
    _save_cv_with_internal_fields(monkeypatch)

    def rebuild_fail(user_id: str) -> dict:
        raise RuntimeError("falha controlada")

    monkeypatch.setattr("main.rebuild_vectorstore_for_user", rebuild_fail)
    failed = _upload(client, session, "falha.txt")
    assert failed.status_code == 200
    failed_body = failed.json()
    _assert_upload_hides_internal_fields(failed_body)
    assert failed_body["filename"] == "falha.txt"
    assert failed_body["content_type"] == "text/plain"
    assert failed_body["bytes_received"] == len(CV_TEXT.encode("utf-8"))
    assert "updated_at" in failed_body
    assert failed_body["document_id"]
    assert failed_body["has_cv"] is True
    assert failed_body["has_embeddings"] is False
    assert failed_body["ready_for_analysis"] is False
    assert failed_body["reason"] == "Nao foi possivel gerar os embeddings do curriculo"
    assert "embeddings" not in failed_body

    def rebuild_ok(user_id: str) -> dict:
        _mark_embeddings(user_id)
        return {
            "user_id": user_id,
            "embedding_run_id": "run-upload",
            "chunks": 4,
            "processed_at": "2026-06-01T00:00:00+00:00",
            "embedding_model": "text-embedding-3-small",
            "chroma_dir": SECRET_CHROMA,
            "vector_store": "chroma",
            "cv_file": SECRET_CV_PATH,
        }

    monkeypatch.setattr("main.rebuild_vectorstore_for_user", rebuild_ok)
    ready = _upload(client, session, "pronto.txt")
    assert ready.status_code == 200
    ready_body = ready.json()
    _assert_upload_hides_internal_fields(ready_body)
    assert ready_body["filename"] == "pronto.txt"
    assert ready_body["bytes_received"] == len(CV_TEXT.encode("utf-8"))
    assert ready_body["has_cv"] is True
    assert ready_body["has_embeddings"] is True
    assert ready_body["ready_for_analysis"] is True
    assert "reason" not in ready_body
    assert ready_body["embeddings"]["chunks"] == 4
    assert ready_body["embeddings"]["embedding_run_id"] == "run-upload"
    assert ready_body["embeddings"]["processed_at"] == "2026-06-01T00:00:00+00:00"
    assert ready_body["embeddings"]["embedding_model"] == "text-embedding-3-small"
    assert SECRET_CHROMA not in ready.text
    assert "vector_store" not in ready_body["embeddings"]


def test_rebuild_embeddings_hides_internal_paths(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "cv.rebuild@example.com")

    def rebuild_ok(user_id: str) -> dict:
        return {
            "user_id": user_id,
            "embedding_run_id": "run-9",
            "chunks": 3,
            "processed_at": "2026-10-19T12:00:00+00:00",
            "embedding_model": "text-embedding-3-small",
            "chroma_dir": SECRET_CHROMA,
            "vector_store": "chroma",
            "cv_file": SECRET_CV_PATH,
        }

    monkeypatch.setattr("main.rebuild_vectorstore_for_user", rebuild_ok)
    response = client.post("/users/me/rebuild-embeddings", headers=session["auth"])
    assert response.status_code == 200
    body = response.json()
    assert body["chunks"] == 3
    assert body["embedding_run_id"] == "run-9"
    assert body["processed_at"] == "2026-10-19T12:00:00+00:00"
    assert body["embedding_model"] == "text-embedding-3-small"
    assert body["user_id"] == session["user_id"]
    assert "chroma_dir" not in body
    assert "vector_store" not in body
    assert "cv_file" not in body
    assert SECRET_CHROMA not in response.text
    assert SECRET_CV_PATH not in response.text


def test_manual_profile_embeddings_hide_internal_paths(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "cv.manual@example.com")
    monkeypatch.setattr(
        "main.save_manual_profile",
        lambda payload, user_id: {
            "user_id": user_id,
            "version": 1,
            "updated_at": "2026-10-19T12:00:00+00:00",
            "document_id": "doc-manual",
            "cv_file": "/perfil/cv.txt",
            "object_key": "users/perfil/documents/cv.txt",
        },
    )
    monkeypatch.setattr(
        "main.rebuild_vectorstore_for_user",
        lambda user_id: {
            "user_id": user_id,
            "embedding_run_id": "run-manual",
            "chunks": 2,
            "processed_at": "2026-10-19T12:00:00+00:00",
            "embedding_model": "text-embedding-3-small",
            "chroma_dir": SECRET_CHROMA,
            "vector_store": "chroma",
            "cv_file": SECRET_CV_PATH,
        },
    )

    response = client.post(
        "/users/me/manual-profile",
        headers=session["auth"],
        json={
            "resumo_profissional": "Analista com experiencia em dados, python e processos.",
            "formacoes": [{"instituicao": "UF", "curso": "Computacao"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "cv_file" not in body
    assert "object_key" not in body
    embeddings = body["embeddings"]
    assert embeddings["chunks"] == 2
    assert embeddings["embedding_run_id"] == "run-manual"
    assert embeddings["embedding_model"] == "text-embedding-3-small"
    assert "chroma_dir" not in embeddings
    assert "vector_store" not in embeddings
    assert "cv_file" not in embeddings
    assert SECRET_CHROMA not in response.text
    assert SECRET_CV_PATH not in response.text


class _FakeCollection:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def insert(self, document: dict) -> None:
        self.rows.append(dict(document))

    def find_one(self, filt, sort=None, projection=None):
        matches = [row for row in self.rows if _matches(row, filt)]
        if not matches:
            return None
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda row, field=field: (row.get(field) is None, row.get(field)),
                    reverse=direction < 0,
                )
        return _project(matches[0], projection)


def _matches(row: dict, filt: dict) -> bool:
    for key, expected in filt.items():
        if key == "$or":
            if not any(_matches(row, clause) for clause in expected):
                return False
            continue
        if row.get(key) != expected:
            return False
    return True


def _project(row: dict, projection: dict | None) -> dict:
    if not projection:
        return dict(row)
    include_id = projection.get("_id", 1) != 0
    projected = {
        key: row.get(key)
        for key, flag in projection.items()
        if key != "_id" and flag
    }
    if include_id:
        projected["_id"] = row.get("_id")
    return projected


def test_mongo_cv_metadata_uses_latest_document_for_that_user(monkeypatch):
    documents = _FakeCollection()
    runs = _FakeCollection()
    collections = {"user_documents": documents, "embedding_runs": runs}
    monkeypatch.setattr(
        "database.mongo_repository._get_collection",
        lambda name: collections[name],
    )
    monkeypatch.setattr("database.repository.PERSISTENCE_BACKEND", "mongodb")
    monkeypatch.setattr("database.mongo_repository.is_configured", lambda: True)

    documents.insert({
        "_id": "doc-old",
        "user_id": "user-a",
        "document_type": "cv",
        "original_filename": "antigo.pdf",
        "original_content_type": "application/pdf",
        "bytes_received": 11,
        "created_at": "2026-10-01T00:00:00+00:00",
        "object_key": SECRET_OBJECT_KEY,
        "original_file_path": SECRET_ORIGINAL,
        "extracted_text_path": SECRET_CV_PATH,
    })
    documents.insert({
        "_id": "doc-new",
        "user_id": "user-a",
        "document_type": "cv",
        "original_filename": "novo.pdf",
        "original_content_type": "application/pdf",
        "bytes_received": 22,
        "created_at": "2026-10-02T00:00:00+00:00",
        "object_key": SECRET_OBJECT_KEY,
        "original_file_path": SECRET_ORIGINAL,
    })
    documents.insert({
        "_id": "doc-b",
        "user_id": "user-b",
        "document_type": "cv",
        "original_filename": "vizinho.pdf",
        "original_content_type": "text/plain",
        "bytes_received": 3,
        "created_at": "2026-10-03T00:00:00+00:00",
    })
    runs.insert({
        "_id": "run-old",
        "user_id": "user-a",
        "document_id": "doc-old",
        "status": "completed",
        "processed_at": "2026-10-01T01:00:00+00:00",
        "chroma_dir": SECRET_CHROMA,
        "cv_file_path": SECRET_CV_PATH,
    })
    runs.insert({
        "_id": "run-other-user",
        "user_id": "user-b",
        "document_id": "doc-new",
        "status": "failed",
        "processed_at": "2026-10-02T02:00:00+00:00",
        "error_message": SECRET_ERROR,
        "chroma_dir": SECRET_CHROMA,
    })

    from database.repository import get_latest_user_cv_metadata

    none_body = get_latest_user_cv_metadata("user-a")
    assert none_body is not None
    _assert_cv_contract(none_body)
    assert none_body["filename"] == "novo.pdf"
    assert none_body["content_type"] == "application/pdf"
    assert none_body["bytes"] == 22
    assert none_body["embedding_status"] == "none"
    assert none_body["uploaded_at"] == "2026-10-02T00:00:00+00:00"

    runs.insert({
        "_id": "run-failed",
        "user_id": "user-a",
        "document_id": "doc-new",
        "status": "failed",
        "processed_at": "2026-10-02 01:00:00",
        "chroma_dir": SECRET_CHROMA,
        "error_message": SECRET_ERROR,
    })
    runs.insert({
        "_id": "run-completed",
        "user_id": "user-a",
        "document_id": "doc-new",
        "status": "completed",
        "processed_at": "2026-10-02 03:00:00",
        "chroma_dir": SECRET_CHROMA,
    })
    completed = get_latest_user_cv_metadata("user-a")
    assert completed is not None
    _assert_cv_contract(completed)
    assert completed["embedding_status"] == "completed"
    assert completed["processed_at"] == "2026-10-02T03:00:00+00:00"

    neighbor = get_latest_user_cv_metadata("user-b")
    assert neighbor is not None
    assert neighbor["filename"] == "vizinho.pdf"
    assert neighbor["embedding_status"] == "none"
    assert neighbor["processed_at"] is None
    assert "novo.pdf" not in str(neighbor)
    assert get_latest_user_cv_metadata("user-c") is None


def test_stored_sqlite_paths_stay_out_of_cv_response(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "cv.paths@example.com")
    monkeypatch.setattr("main.save_user_cv", _load_real_save_user_cv())
    monkeypatch.setattr("main.rebuild_vectorstore_for_user", lambda user_id: {"chunks": 0})
    uploaded = _upload(client, session, "curriculo.txt")
    assert uploaded.status_code == 200
    document_id = uploaded.json()["document_id"]

    connection = sqlite3.connect(isolated_db)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT original_file_path, object_key, extracted_text_path
        FROM user_documents
        WHERE document_id = ?
        """,
        (document_id,),
    ).fetchone()
    connection.close()
    assert row is not None

    response = client.get("/users/me/cv", headers=session["auth"])
    assert response.status_code == 200
    assert row["original_file_path"] not in response.text
    assert row["object_key"] not in response.text
    assert row["extracted_text_path"] not in response.text
