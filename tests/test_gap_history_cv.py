"""Gap-history aponta o CV do dono e o download não vaza arquivo alheio."""

from __future__ import annotations

import json
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from config import (
    CURRENT_PRIVACY_VERSION,
    CURRENT_TERMS_VERSION,
    get_user_output_dir,
)
from database.generated_file_names import is_safe_generated_file_name
from database.repository import (
    create_generated_file,
    create_job_analysis_insight,
    create_processing_run,
)
from services.object_storage import put_bytes, user_object_key


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


def _client() -> TestClient:
    _install_heavy_service_stubs()
    from main import app

    return TestClient(app)


def _register(client: TestClient, email: str) -> dict[str, object]:
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Usuario Historico",
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


def _processing_run(user_id: object, text: str) -> int | str:
    return create_processing_run(
        {
            "user_id": user_id,
            "input_text": text,
            "job_data": {"cargo": "Analista"},
            "matching": None,
            "optimization": None,
            "response_text": "analise",
            "status": "completed",
            "error_message": None,
            "completed_at": "2026-09-01T00:00:00+00:00",
        },
    )


def _insight(
    user_id: object,
    processing_run_id: int | str,
    *,
    created_at: str,
    blocked: bool = False,
    job_summary: str = "Vaga de analista",
) -> None:
    create_job_analysis_insight(
        {
            "user_id": user_id,
            "processing_run_id": processing_run_id,
            "job_title": "Analista",
            "company_name": "Acme",
            "job_summary": job_summary,
            "match_score": 40 if blocked else 82,
            "strengths": ["SQL"],
            "critical_gaps": ["Power BI"],
            "matching_skills": ["SQL"],
            "missing_skills": ["Power BI"],
            "status": "completed",
            "generation_blocked": blocked,
            "blocked_reason": "low_match_score" if blocked else None,
            "source": "processar",
            "created_at": created_at,
        },
    )


def _generated_pdf(
    user_id: object,
    processing_run_id: int | str | None,
    file_name: str,
    *,
    pdf_bytes: bytes = b"%PDF-1.4 curriculo",
    write_disk: bool = True,
) -> str:
    internal_path = f"/var/lib/fatia/chroma/{user_id}/{file_name}"
    public_url = f"https://files.example/permanent/{file_name}"
    object_key = f"users/other-tenant/outputs/{file_name}"
    create_generated_file(
        {
            "user_id": user_id,
            "processing_run_id": processing_run_id,
            "file_name": file_name,
            "file_path": internal_path,
            "object_key": object_key,
            "public_url": public_url,
            "media_type": "application/pdf",
            "bytes_size": len(pdf_bytes),
        },
    )
    if write_disk:
        destination = get_user_output_dir(str(user_id)) / file_name
        destination.write_bytes(pdf_bytes)
    return internal_path


def _history(client: TestClient, auth: dict[str, str]) -> dict:
    response = client.get("/users/me/gap-history", headers=auth)
    assert response.status_code == 200
    return response.json()


def _assert_generic_denial(response) -> None:
    assert response.status_code == 404
    body = response.text.lower()
    assert response.json()["detail"] == "Arquivo nao encontrado"
    assert "traceback" not in body
    assert "%pdf" not in body
    assert "/var/lib" not in body
    assert "chroma" not in body
    assert "file_path" not in body
    assert "object_key" not in body


def test_gap_history_with_cv_exposes_owned_download_name(isolated_db) -> None:
    client = _client()
    owner = _register(client, "cv.owner@example.com")
    run_id = _processing_run(owner["user_id"], "Vaga com curriculo")
    file_name = "8f1c0a3e-6b2d-4c9a-9f10-1c2d3e4f5a6b.pdf"
    pdf_bytes = b"%PDF-1.4 curriculo-do-dono"
    internal_path = _generated_pdf(
        owner["user_id"],
        run_id,
        file_name,
        pdf_bytes=pdf_bytes,
    )
    _insight(owner["user_id"], run_id, created_at="2026-09-02T00:00:00+00:00")

    body = _history(client, owner["auth"])
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["cv_file_name"] == file_name
    assert item["pdf_url"] == f"/users/me/files/{file_name}"
    assert "://" not in item["pdf_url"]
    serialized = json.dumps(body)
    assert internal_path not in serialized
    assert "https://files.example" not in serialized
    assert "chroma" not in serialized
    assert "file_path" not in item
    assert "object_key" not in item

    downloaded = client.get(item["pdf_url"], headers=owner["auth"])
    assert downloaded.status_code == 200
    assert downloaded.content == pdf_bytes
    assert downloaded.headers["content-type"].startswith("application/pdf")
    assert internal_path not in downloaded.headers.get("content-disposition", "")


def test_gap_history_without_pdf_and_cover_letter_are_null(isolated_db) -> None:
    client = _client()
    owner = _register(client, "cv.missing@example.com")

    blocked_run = _processing_run(owner["user_id"], "Vaga bloqueada")
    _insight(
        owner["user_id"],
        blocked_run,
        created_at="2026-09-03T00:00:00+00:00",
        blocked=True,
        job_summary="Vaga bloqueada",
    )

    letter_run = _processing_run(owner["user_id"], "Vaga so com carta")
    _generated_pdf(
        owner["user_id"],
        letter_run,
        "carta-apresentacao-11111111-2222-4333-8444-555555555555.pdf",
        pdf_bytes=b"%PDF-1.4 carta",
    )
    _insight(
        owner["user_id"],
        letter_run,
        created_at="2026-09-02T00:00:00+00:00",
        job_summary="Vaga so com carta",
    )

    both_run = _processing_run(owner["user_id"], "Vaga com carta e cv")
    _generated_pdf(
        owner["user_id"],
        both_run,
        "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.pdf",
        pdf_bytes=b"%PDF-1.4 cv-antigo",
    )
    _generated_pdf(
        owner["user_id"],
        both_run,
        "carta-apresentacao-99999999-8888-4777-8666-555555555555.pdf",
        pdf_bytes=b"%PDF-1.4 carta-nova",
    )
    _insight(
        owner["user_id"],
        both_run,
        created_at="2026-09-01T00:00:00+00:00",
        job_summary="Vaga com carta e cv",
    )

    items = _history(client, owner["auth"])["items"]
    by_summary = {item["job_summary"]: item for item in items}
    blocked = by_summary["Vaga bloqueada"]
    letter_only = by_summary["Vaga so com carta"]
    both = by_summary["Vaga com carta e cv"]

    assert blocked["cv_file_name"] is None
    assert blocked["pdf_url"] is None
    assert letter_only["cv_file_name"] is None
    assert letter_only["pdf_url"] is None
    assert both["cv_file_name"] == "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.pdf"
    assert both["pdf_url"] == "/users/me/files/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee.pdf"
    assert "carta-apresentacao-" not in json.dumps(
        {"cv_file_name": both["cv_file_name"], "pdf_url": both["pdf_url"]},
    )


def test_gap_history_does_not_leak_another_users_cv(isolated_db) -> None:
    client = _client()
    owner = _register(client, "cv.private@example.com")
    other = _register(client, "cv.other@example.com")
    run_id = _processing_run(owner["user_id"], "Vaga privada")
    secret_name = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff.pdf"
    _generated_pdf(owner["user_id"], run_id, secret_name, pdf_bytes=b"%PDF-1.4 segredo")
    _insight(owner["user_id"], run_id, created_at="2026-09-04T00:00:00+00:00")
    _insight(other["user_id"], run_id, created_at="2026-09-04T00:00:00+00:00")

    owner_item = _history(client, owner["auth"])["items"][0]
    other_body = _history(client, other["auth"])
    assert owner_item["cv_file_name"] == secret_name
    assert len(other_body["items"]) == 1
    assert other_body["items"][0]["cv_file_name"] is None
    assert other_body["items"][0]["pdf_url"] is None
    assert secret_name not in json.dumps(other_body)

    denied = client.get(f"/users/me/files/{secret_name}", headers=other["auth"])
    _assert_generic_denial(denied)


def test_download_requires_owner_jwt_and_registered_name(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("auth.AUTH_MODE", "header")
    monkeypatch.setattr("config.AUTH_MODE", "header")
    client = _client()
    owner = _register(client, "download.owner@example.com")
    other = _register(client, "download.other@example.com")
    file_name = "curriculo-registrado.pdf"
    pdf_bytes = b"%PDF-1.4 registrado"
    _generated_pdf(owner["user_id"], None, file_name, pdf_bytes=pdf_bytes)
    loose = get_user_output_dir(str(owner["user_id"])) / "solto-no-disco.pdf"
    loose.write_bytes(b"%PDF-1.4 solto")
    poisoned_key = user_object_key(str(other["user_id"]), "outputs", "vizinho.pdf")
    put_bytes(poisoned_key, b"%PDF-1.4 vizinho", "application/pdf")

    owned = client.get(f"/users/me/files/{file_name}", headers=owner["auth"])
    assert owned.status_code == 200
    assert owned.content == pdf_bytes

    only_header = client.get(
        f"/users/me/files/{file_name}",
        headers={"X-User-Id": str(owner["user_id"])},
    )
    assert only_header.status_code == 401
    assert b"%PDF" not in only_header.content

    swapped = client.get(
        f"/users/me/files/{file_name}",
        headers={**other["auth"], "X-User-Id": str(owner["user_id"])},
    )
    _assert_generic_denial(swapped)

    unregistered = client.get("/users/me/files/solto-no-disco.pdf", headers=owner["auth"])
    _assert_generic_denial(unregistered)

    neighbor = client.get("/users/me/files/vizinho.pdf", headers=owner["auth"])
    _assert_generic_denial(neighbor)


@pytest.mark.parametrize(
    "raw_name",
    [
        "..",
        "../cv.txt",
        "..\\cv.txt",
        "foo/bar.pdf",
        "foo\\bar.pdf",
        "cv..pdf",
        "nota.txt",
        "carta-apresentacao-../x.pdf",
        " %20cv.pdf",
        "cv.pdf ",
    ],
)
def test_generated_file_name_allowlist_blocks_traversal(raw_name: str) -> None:
    assert is_safe_generated_file_name(raw_name) is False


def test_download_rejects_traversal_without_leaking_payload(isolated_db) -> None:
    client = _client()
    owner = _register(client, "download.traversal@example.com")
    secret = get_user_output_dir(str(owner["user_id"])).parent / "cv.txt"
    secret.write_text("conteudo-secreto-do-cv", encoding="utf-8")

    for raw_name in ("cv..pdf", "foo\\bar.pdf", "..\\cv.txt"):
        response = client.get(
            "/users/me/files/" + raw_name,
            headers=owner["auth"],
        )
        assert response.status_code == 404
        text = response.text.lower()
        assert "traceback" not in text
        assert "conteudo-secreto" not in text
        assert "cv.txt" not in text
        assert "/var/" not in text
        assert str(secret) not in response.text
