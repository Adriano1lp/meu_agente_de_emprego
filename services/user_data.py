from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from config import (
    MAX_UPLOAD_SIZE_MB,
    get_user_cv_file,
    get_user_documents_dir,
    get_user_profile_file,
    get_user_profile_versions_file,
)
from database.repository import (
    create_user_document,
    get_user_profile as get_persisted_user_profile,
    upsert_user_profile,
)
from services.object_storage import put_bytes, user_object_key

ALLOWED_CV_EXTENSIONS = {".txt", ".pdf"}


def save_user_cv(upload_file: UploadFile, user_id: str) -> dict[str, Any]:
    file_extension = Path(upload_file.filename or "").suffix.lower()
    if file_extension not in ALLOWED_CV_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Formato de arquivo invalido. Envie um arquivo .txt ou .pdf",
        )

    file_bytes = upload_file.file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Arquivo enviado esta vazio")

    file_size = len(file_bytes)
    max_upload_size_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if file_size > max_upload_size_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"Arquivo excede o limite de {MAX_UPLOAD_SIZE_MB} MB",
        )

    extracted_text = _extract_cv_text(file_bytes, file_extension)
    cv_text = extracted_text.strip()
    if not cv_text:
        raise HTTPException(
            status_code=400,
            detail="Nao foi possivel extrair texto do arquivo enviado",
        )

    documents_dir = get_user_documents_dir(user_id)
    original_file_path = documents_dir / f"cv_original{file_extension}"
    original_file_path.write_bytes(file_bytes)
    original_object_key = user_object_key(
        user_id,
        "documents",
        original_file_path.name,
    )
    put_bytes(
        original_object_key,
        file_bytes,
        upload_file.content_type or "application/octet-stream",
    )

    cv_file = get_user_cv_file(user_id)
    cv_file.write_text(cv_text, encoding="utf-8")
    put_bytes(user_object_key(user_id, "documents", cv_file.name), cv_text.encode("utf-8"), "text/plain")

    updated_at = _utc_now_iso()
    document_id = create_user_document(
        {
            "user_id": user_id,
            "document_type": "cv",
            "original_filename": upload_file.filename or original_file_path.name,
            "original_content_type": upload_file.content_type or "application/octet-stream",
            "original_file_path": str(original_file_path),
            "object_key": original_object_key,
            "extracted_text_path": str(cv_file),
            "extracted_text": cv_text,
            "bytes_received": file_size,
            "checksum_sha256": hashlib.sha256(file_bytes).hexdigest(),
        },
    )

    return {
        "user_id": user_id,
        "document_id": document_id,
        "filename": upload_file.filename or cv_file.name,
        "content_type": upload_file.content_type or "application/octet-stream",
        "bytes_received": file_size,
        "updated_at": updated_at,
        "cv_file": str(cv_file),
        "original_file": str(original_file_path),
    }


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    persisted_profile = get_persisted_user_profile(user_id)
    if persisted_profile:
        return persisted_profile

    profile_file = get_user_profile_file(user_id)
    if not profile_file.exists():
        return None

    return json.loads(profile_file.read_text(encoding="utf-8"))


def save_user_profile(profile_data: dict[str, Any], user_id: str) -> dict[str, Any]:
    profile_file = get_user_profile_file(user_id)
    versions_file = get_user_profile_versions_file(user_id)
    current_profile = get_user_profile(user_id)

    next_version = 1
    if current_profile:
        next_version = int(current_profile.get("version", 0)) + 1

    payload = {
        "user_id": user_id,
        "version": next_version,
        "updated_at": _utc_now_iso(),
        "profile": profile_data,
    }

    profile_file.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    with versions_file.open("a", encoding="utf-8") as history:
        history.write(json.dumps(payload, ensure_ascii=True) + "\n")

    upsert_user_profile(user_id, next_version, profile_data)

    return payload


def save_manual_profile(profile_data: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Persist a structured profile and expose a traceable text source to the RAG."""
    saved_profile = save_user_profile(profile_data, user_id)
    profile_text = build_manual_profile_text(profile_data)
    cv_file = get_user_cv_file(user_id)
    cv_file.write_text(profile_text, encoding="utf-8")

    profile_bytes = profile_text.encode("utf-8")
    version = saved_profile["version"]
    document_id = create_user_document(
        {
            "user_id": user_id,
            "document_type": "cv",
            "original_filename": f"manual-profile-v{version}.txt",
            "original_content_type": "text/plain; charset=utf-8",
            "original_file_path": str(cv_file),
            "extracted_text_path": str(cv_file),
            "extracted_text": profile_text,
            "bytes_received": len(profile_bytes),
            "checksum_sha256": hashlib.sha256(profile_bytes).hexdigest(),
        },
    )
    return {**saved_profile, "document_id": document_id, "cv_file": str(cv_file)}


def build_manual_profile_text(profile_data: dict[str, Any]) -> str:
    sections: list[str] = []

    def add_section(title: str, value: Any) -> None:
        lines = _profile_value_lines(value)
        if lines:
            sections.append(f"{title}\n" + "\n".join(lines))

    add_section("DADOS PROFISSIONAIS", {
        "titulo_profissional": profile_data.get("titulo_profissional"),
        "senioridade": profile_data.get("senioridade"),
        "modelo_trabalho": profile_data.get("modelo_trabalho"),
        "disponibilidade": profile_data.get("disponibilidade"),
    })
    add_section("RESUMO PROFISSIONAL", profile_data.get("resumo_profissional"))
    add_section("OBJETIVOS E AREAS DE INTERESSE", profile_data.get("objetivos_profissionais"))
    add_section("FORMACAO ACADEMICA", profile_data.get("formacoes"))
    add_section("EXPERIENCIA PROFISSIONAL", profile_data.get("experiencias"))
    add_section("HABILIDADES TECNICAS", profile_data.get("habilidades_tecnicas"))
    add_section("FERRAMENTAS E METODOS", profile_data.get("ferramentas"))
    add_section("IDIOMAS", profile_data.get("idiomas"))
    add_section("CERTIFICACOES", profile_data.get("certificacoes"))
    add_section("PROJETOS RELEVANTES", profile_data.get("projetos"))
    add_section("ATIVIDADES COMPLEMENTARES", profile_data.get("atividades_complementares"))
    return "\n\n".join(sections).strip()


def _profile_value_lines(value: Any, prefix: str = "") -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [f"{prefix}{cleaned}"] if cleaned else []
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            label = key.replace("_", " ").strip().capitalize()
            nested = _profile_value_lines(item)
            if nested:
                lines.append(f"{label}: " + " | ".join(nested))
        return lines
    if isinstance(value, (list, tuple)):
        lines = []
        for index, item in enumerate(value, start=1):
            nested = _profile_value_lines(item)
            if nested:
                lines.append(f"{index}. " + " | ".join(nested))
        return lines
    return [f"{prefix}{value}"]


def _extract_cv_text(file_bytes: bytes, file_extension: str) -> str:
    if file_extension == ".txt":
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_bytes.decode("latin-1")

    if file_extension == ".pdf":
        return _extract_text_from_pdf(file_bytes)

    raise HTTPException(status_code=400, detail="Formato de arquivo nao suportado")


def _extract_text_from_pdf(file_bytes: bytes) -> str:
    from io import BytesIO

    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail="Leitura de PDF indisponivel. Instale a dependencia pypdf",
        ) from exc

    try:
        reader = PdfReader(BytesIO(file_bytes))
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Nao foi possivel ler o PDF enviado",
        ) from exc

    pages_text: list[str] = []
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""

        if page_text.strip():
            pages_text.append(page_text.strip())

    return "\n\n".join(pages_text)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
