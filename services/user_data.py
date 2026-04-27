from __future__ import annotations

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

    cv_file = get_user_cv_file(user_id)
    cv_file.write_text(cv_text, encoding="utf-8")

    updated_at = _utc_now_iso()
    return {
        "user_id": user_id,
        "filename": upload_file.filename or cv_file.name,
        "content_type": upload_file.content_type or "application/octet-stream",
        "bytes_received": file_size,
        "updated_at": updated_at,
        "cv_file": str(cv_file),
        "original_file": str(original_file_path),
    }


def get_user_profile(user_id: str) -> dict[str, Any] | None:
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

    return payload


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
