"""Forma publica do CV estruturado no pacote de exportacao LGPD."""

from __future__ import annotations

from typing import Any

CHAVES_INTERNAS = frozenset({
    "object_key",
    "extracted_text_object_key",
    "chroma_dir",
    "cv_file",
    "cv_file_path",
    "original_file_path",
    "extracted_text_path",
    "vector_store",
    "document_id",
})


def public_structured_cv_export(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    payload = {
        "version": record.get("version"),
        "data": record.get("data"),
        "consent": {
            "versao_termo": record.get("consent_version"),
            "accepted_at": record.get("consent_accepted_at"),
        },
        "canonical_sha256": record.get("canonical_sha256"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }
    return _sem_chaves_internas(payload)


def _sem_chaves_internas(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sem_chaves_internas(item)
            for key, item in value.items()
            if str(key) not in CHAVES_INTERNAS
        }
    if isinstance(value, list):
        return [_sem_chaves_internas(item) for item in value]
    return value
