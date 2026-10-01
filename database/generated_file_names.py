"""Nomes seguros dos PDFs registrados em generated_files."""

from __future__ import annotations

import re

COVER_LETTER_FILE_PREFIX = "carta-apresentacao-"
_GENERATED_PDF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.pdf\Z")


def is_safe_generated_file_name(file_name: str | None) -> bool:
    """Aceita só basename PDF, sem travessia nem separadores."""
    if not isinstance(file_name, str):
        return False
    if file_name != file_name.strip():
        return False
    if any(token in file_name for token in ("..", "/", "\\", "\x00")):
        return False
    return _GENERATED_PDF_NAME.fullmatch(file_name) is not None


def is_cv_file_name(file_name: str | None) -> bool:
    """CV gerado em /processar. Carta usa o prefixo carta-apresentacao-."""
    if not is_safe_generated_file_name(file_name):
        return False
    assert file_name is not None
    return not file_name.lower().startswith(COVER_LETTER_FILE_PREFIX)


def authenticated_pdf_path(file_name: str) -> str:
    return f"/users/me/files/{file_name}"


def cv_download_fields(file_name: str | None) -> dict[str, str | None]:
    """Referência de download do CV. pdf_url é path relativo autenticado."""
    if not is_cv_file_name(file_name):
        return {"cv_file_name": None, "pdf_url": None}
    assert file_name is not None
    return {
        "cv_file_name": file_name,
        "pdf_url": authenticated_pdf_path(file_name),
    }
