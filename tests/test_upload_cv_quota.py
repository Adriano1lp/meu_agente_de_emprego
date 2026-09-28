from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

from fastapi import HTTPException

from config import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION, get_user_chroma_dir, get_user_cv_file
from database.repository import get_processar_usage
from services.billing import consume_processar_quota, current_usage_period

CV_TEXT = "Curriculo de teste com experiencia em Python e analise de dados."
SECRET_EMBEDDING_ERROR = "openai-timeout-cv-chunk-nao-logar"


def _install_heavy_service_stubs() -> None:
    stubs = {
        "services.main_chat": {
            "generate_cover_letter": MagicMock(),
            "pipeline_with_details": MagicMock(
                return_value={
                    "resposta_usuario": "Analise ok",
                    "match_score": 80,
                    "should_generate_curriculum": False,
                    "vaga": {"cargo": "Dev"},
                    "matching": {"pontos_fortes": ["Python"]},
                    "otimizacao": {},
                }
            ),
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


def _load_real_save_user_cv():
    module_name = "real_user_data_for_upload_tests"
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
            "display_name": "Usuario Upload",
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
        "token": body["access_token"],
    }


def _mark_embeddings(user_id: str) -> None:
    chroma_dir = get_user_chroma_dir(user_id)
    (chroma_dir / "index").write_text("embeddings", encoding="utf-8")


def _pipeline_ok() -> MagicMock:
    return MagicMock(
        return_value={
            "resposta_usuario": "Analise ok",
            "match_score": 80,
            "should_generate_curriculum": False,
            "vaga": {"cargo": "Dev"},
            "matching": {"pontos_fortes": ["Python"]},
            "otimizacao": {},
        }
    )


def _upload(client, session, filename: str = "cv.txt"):
    return client.post(
        "/users/me/upload-cv",
        headers=session["auth"],
        files={"file": (filename, CV_TEXT.encode("utf-8"), "text/plain")},
    )


def _saved_cv_fields(body: dict, session: dict) -> None:
    assert body["user_id"] == session["user_id"]
    assert body["document_id"]
    assert body["filename"] == "cv.txt"
    assert body["bytes_received"] == len(CV_TEXT.encode("utf-8"))
    assert "updated_at" in body
    assert "cv_file" in body
    assert "original_file" in body
    assert "object_key" in body
    cv_file = get_user_cv_file(session["user_id"])
    assert cv_file.exists()
    assert cv_file.read_text(encoding="utf-8") == CV_TEXT


def _usage(user_id: str) -> int:
    return get_processar_usage(user_id, current_usage_period())


NOT_READY_DETAIL = (
    "Curriculo nao esta pronto para analise. "
    "Embeddings do usuario nao encontrados. "
    "Envie o curriculo e execute POST /users/me/rebuild-embeddings antes de processar a vaga."
)


def _replace_embeddings(user_id: str, chunks: int = 2) -> dict:
    chroma_dir = get_user_chroma_dir(user_id)
    (chroma_dir / "index").write_text("embeddings", encoding="utf-8")
    return {"user_id": user_id, "chunks": chunks, "vector_store": "chroma"}


def _bind_rebuild(monkeypatch, rebuild):
    monkeypatch.setattr("main.save_user_cv", _load_real_save_user_cv())
    monkeypatch.setattr("main.rebuild_vectorstore_for_user", rebuild)


def _assert_log_has_no_personal_data(logged: str, session: dict) -> None:
    assert SECRET_EMBEDDING_ERROR not in logged
    assert CV_TEXT not in logged
    assert session["token"] not in logged
    assert session["user_id"] not in logged
    assert "Usuario Upload" not in logged
    assert "@example.com" not in logged


def test_c1_upload_ready_without_calling_rebuild_endpoint(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "c1.upload@example.com")
    before = client.get("/users/me/status", headers=session["auth"])
    assert before.status_code == 200
    assert before.json()["has_cv"] is False
    assert before.json()["has_embeddings"] is False

    calls = {"n": 0}

    def rebuild_ok(user_id: str) -> dict:
        calls["n"] += 1
        return _replace_embeddings(user_id, chunks=2)

    _bind_rebuild(monkeypatch, rebuild_ok)
    response = _upload(client, session)
    assert response.status_code == 200
    body = response.json()
    _saved_cv_fields(body, session)
    assert body["ready_for_analysis"] is True
    assert body["has_cv"] is True
    assert body["has_embeddings"] is True
    assert body["embeddings"]["chunks"] == 2
    assert "reason" not in body
    assert calls["n"] == 1

    status = client.get("/users/me/status", headers=session["auth"]).json()
    assert status["has_cv"] is True
    assert status["has_embeddings"] is True


def test_c2_embedding_failure_keeps_cv_and_later_rebuild_recovers(isolated_db, monkeypatch, caplog):
    client = _client()
    session = _register(client, "c2.upload@example.com")
    _mark_embeddings(session["user_id"])
    calls = {"n": 0}

    def rebuild_fail(user_id: str) -> dict:
        calls["n"] += 1
        raise RuntimeError(SECRET_EMBEDDING_ERROR)

    _bind_rebuild(monkeypatch, rebuild_fail)
    with caplog.at_level(logging.ERROR, logger="main"):
        response = _upload(client, session)

    assert response.status_code == 200
    body = response.json()
    _saved_cv_fields(body, session)
    assert body["has_cv"] is True
    assert body["ready_for_analysis"] is False
    assert body["has_embeddings"] is False
    assert body["reason"] == "Nao foi possivel gerar os embeddings do curriculo"
    assert SECRET_EMBEDDING_ERROR not in response.text
    assert CV_TEXT not in response.text
    assert session["token"] not in response.text
    _assert_log_has_no_personal_data(
        " ".join(record.getMessage() for record in caplog.records),
        session,
    )
    assert "RuntimeError" in " ".join(record.getMessage() for record in caplog.records)

    status = client.get("/users/me/status", headers=session["auth"]).json()
    assert status["has_cv"] is True
    assert status["has_embeddings"] is False
    assert _usage(session["user_id"]) == 0

    def rebuild_ok(user_id: str) -> dict:
        calls["n"] += 1
        return _replace_embeddings(user_id, chunks=4)

    monkeypatch.setattr("main.rebuild_vectorstore_for_user", rebuild_ok)
    first = client.post("/users/me/rebuild-embeddings", headers=session["auth"])
    second = client.post("/users/me/rebuild-embeddings", headers=session["auth"])
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["chunks"] == 4
    assert second.json()["chunks"] == 4
    recovered = client.get("/users/me/status", headers=session["auth"]).json()
    assert recovered["has_cv"] is True
    assert recovered["has_embeddings"] is True
    assert _usage(session["user_id"]) == 0


def test_upload_cv_hides_embedding_http_error(isolated_db, monkeypatch, caplog):
    client = _client()
    session = _register(client, "upload.http-fail@example.com")

    def rebuild_fail(user_id: str) -> dict:
        raise HTTPException(status_code=400, detail=SECRET_EMBEDDING_ERROR)

    _bind_rebuild(monkeypatch, rebuild_fail)
    with caplog.at_level(logging.ERROR, logger="main"):
        response = _upload(client, session)

    assert response.status_code == 200
    body = response.json()
    _saved_cv_fields(body, session)
    assert body["ready_for_analysis"] is False
    assert body["has_embeddings"] is False
    assert SECRET_EMBEDDING_ERROR not in response.text
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "HTTPException" in logged
    _assert_log_has_no_personal_data(logged, session)


def test_c3_cv_not_ready_precedes_402_and_does_not_change_quota(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "c3.not-ready@example.com")
    for _ in range(5):
        consume_processar_quota(session["user_id"])
    assert _usage(session["user_id"]) == 5
    pipeline = _pipeline_ok()
    monkeypatch.setattr("main.pipeline_with_details", pipeline)

    response = client.post(
        "/processar",
        headers=session["auth"],
        json={"texto": "Vaga para desenvolvedor Python"},
    )
    assert 400 <= response.status_code < 500
    assert response.status_code != 402
    assert response.json()["detail"] == NOT_READY_DETAIL
    assert "nao esta pronto" in response.json()["detail"]
    assert _usage(session["user_id"]) == 5
    assert pipeline.call_count == 0


def test_c4_pipeline_failure_keeps_previous_quota(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "c4.pipeline-fail@example.com")
    _mark_embeddings(session["user_id"])
    consume_processar_quota(session["user_id"])
    consume_processar_quota(session["user_id"])
    assert _usage(session["user_id"]) == 2
    pipeline = MagicMock(side_effect=RuntimeError("llm timeout"))
    monkeypatch.setattr("main.pipeline_with_details", pipeline)

    failed = client.post(
        "/processar",
        headers=session["auth"],
        json={"texto": "Vaga que estoura o modelo"},
    )
    assert failed.status_code == 500
    assert failed.json()["detail"] == "Erro interno ao processar a vaga"
    assert "llm timeout" not in failed.text
    assert _usage(session["user_id"]) == 2
    assert pipeline.call_count == 1


def test_c5_success_debits_exactly_one(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "c5.success@example.com")
    _mark_embeddings(session["user_id"])
    pipeline = _pipeline_ok()
    monkeypatch.setattr("main.pipeline_with_details", pipeline)

    response = client.post(
        "/processar",
        headers=session["auth"],
        json={"texto": "Vaga para analista de dados"},
    )
    assert response.status_code == 200
    assert _usage(session["user_id"]) == 1
    assert pipeline.call_count == 1


def test_c6_upload_then_rebuild_is_idempotent_without_touching_other_flows(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "c6.regression@example.com")
    monkeypatch.setattr("main.generate_cover_letter", lambda empresa, user_id: "Carta pronta")

    def history():
        response = client.get("/users/me/gap-history", headers=session["auth"])
        assert response.status_code == 200
        return response.json()

    def cover_letter():
        response = client.post(
            "/users/me/cover-letter",
            headers=session["auth"],
            json={"empresa": "ACME"},
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"texto_resposta", "pdf_url", "user_id"}
        assert body["texto_resposta"] == "Carta pronta"
        assert body["user_id"] == session["user_id"]
        assert "carta-apresentacao-" in body["pdf_url"]
        return body

    history_before = history()
    cover_before = cover_letter()
    assert history_before == {"items": [], "limit": 20, "offset": 0}
    assert _usage(session["user_id"]) == 0

    calls = {"n": 0}

    def rebuild_ok(user_id: str) -> dict:
        calls["n"] += 1
        return _replace_embeddings(user_id, chunks=2)

    _bind_rebuild(monkeypatch, rebuild_ok)
    uploaded = _upload(client, session)
    assert uploaded.status_code == 200
    assert uploaded.json()["ready_for_analysis"] is True
    first = client.post("/users/me/rebuild-embeddings", headers=session["auth"])
    second = client.post("/users/me/rebuild-embeddings", headers=session["auth"])
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["chunks"] == 2
    assert second.json()["chunks"] == 2
    assert calls["n"] == 3
    assert _usage(session["user_id"]) == 0

    history_after = history()
    cover_after = cover_letter()
    assert history_after == history_before
    assert set(cover_after) == set(cover_before)
    assert cover_after["texto_resposta"] == cover_before["texto_resposta"]
    assert cover_after["user_id"] == cover_before["user_id"]
    status = client.get("/users/me/status", headers=session["auth"]).json()
    assert status["has_cv"] is True
    assert status["has_embeddings"] is True
    assert _usage(session["user_id"]) == 0


def test_c7_exhausted_quota_with_embeddings_returns_402(isolated_db, monkeypatch):
    client = _client()
    session = _register(client, "c7.exhausted@example.com")
    _mark_embeddings(session["user_id"])
    for _ in range(5):
        consume_processar_quota(session["user_id"])
    pipeline = _pipeline_ok()
    monkeypatch.setattr("main.pipeline_with_details", pipeline)

    response = client.post(
        "/processar",
        headers=session["auth"],
        json={"texto": "Vaga alem da cota gratuita"},
    )
    assert response.status_code == 402
    detail = response.json()["detail"]
    assert detail["code"] == "SUBSCRIPTION_REQUIRED"
    assert detail["used"] == 5
    assert detail["limit"] == 5
    assert detail["plan"] == "free"
    assert "message" in detail
    assert _usage(session["user_id"]) == 5
    assert pipeline.call_count == 0
