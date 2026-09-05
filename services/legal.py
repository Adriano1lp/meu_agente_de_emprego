from __future__ import annotations

from fastapi import HTTPException

from config import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION, LEGAL_DOCS_DIR

LEGAL_DOCS = ("terms", "privacy")


def current_version_for(doc: str) -> str:
    if doc == "terms":
        return CURRENT_TERMS_VERSION
    if doc == "privacy":
        return CURRENT_PRIVACY_VERSION
    raise HTTPException(status_code=400, detail="doc deve ser terms ou privacy")


def get_legal_markdown(doc: str, version: str) -> str:
    if doc not in LEGAL_DOCS:
        raise HTTPException(status_code=404, detail="Documento legal nao encontrado")

    cleaned = (version or "").strip()
    if not cleaned or any(char in cleaned for char in "/\\") or ".." in cleaned:
        raise HTTPException(status_code=404, detail="Documento legal nao encontrado")

    path = LEGAL_DOCS_DIR / doc / f"{cleaned}.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Documento legal nao encontrado")

    return path.read_text(encoding="utf-8")
