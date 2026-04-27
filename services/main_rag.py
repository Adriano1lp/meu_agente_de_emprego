from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import (
    OPENAI_EMBEDDING_MODEL,
    get_default_user_chroma_dir,
    get_default_user_cv_file,
    get_user_chroma_dir,
    get_user_cv_file,
    ensure_openai_api_key,
)

OPENAI_API_KEY = ensure_openai_api_key()


def rebuild_vectorstore_for_user(user_id: str) -> dict[str, Any]:
    cv_file = get_user_cv_file(user_id)
    if not cv_file.exists():
        raise HTTPException(
            status_code=400,
            detail=(
                "Curriculo do usuario nao encontrado. "
                "Envie o arquivo antes de gerar embeddings."
            ),
        )

    chroma_dir = get_user_chroma_dir(user_id)
    _reset_directory(chroma_dir)

    embeddings = OpenAIEmbeddings(
        model=OPENAI_EMBEDDING_MODEL,
        openai_api_key=OPENAI_API_KEY,
    )

    loader = TextLoader(str(cv_file), encoding="utf-8")
    documentos = loader.load()
    txt_chunks = _split_documents(documentos, cv_file)
    if not txt_chunks:
        raise HTTPException(
            status_code=400,
            detail="Nao foi possivel gerar chunks validos para o curriculo enviado",
        )

    Chroma.from_documents(
        txt_chunks,
        embedding=embeddings,
        persist_directory=str(chroma_dir),
    )

    return {
        "user_id": user_id,
        "chunks": len(txt_chunks),
        "processed_at": _utc_now_iso(),
        "embedding_model": OPENAI_EMBEDDING_MODEL,
        "chroma_dir": str(chroma_dir),
        "cv_file": str(cv_file),
    }


def rebuild_vectorstore() -> dict[str, Any]:
    return rebuild_vectorstore_legacy(
        cv_file=get_default_user_cv_file(),
        chroma_dir=get_default_user_chroma_dir(),
    )


def rebuild_vectorstore_legacy(cv_file: Path, chroma_dir: Path) -> dict[str, Any]:
    if not cv_file.exists():
        raise FileNotFoundError(f"Arquivo de curriculo nao encontrado em: {cv_file}")

    _reset_directory(chroma_dir)

    embeddings = OpenAIEmbeddings(
        model=OPENAI_EMBEDDING_MODEL,
        openai_api_key=OPENAI_API_KEY,
    )

    loader = TextLoader(str(cv_file), encoding="utf-8")
    documentos = loader.load()
    txt_chunks = _split_documents(documentos, cv_file)

    Chroma.from_documents(
        txt_chunks,
        embedding=embeddings,
        persist_directory=str(chroma_dir),
    )

    return {
        "chunks": len(txt_chunks),
        "processed_at": _utc_now_iso(),
        "embedding_model": OPENAI_EMBEDDING_MODEL,
        "chroma_dir": str(chroma_dir),
        "cv_file": str(cv_file),
    }


def _split_documents(documentos: list[Any], cv_file: Path) -> list[Any]:
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=700,
        chunk_overlap=200,
        separators=["\n\n", "\n", " ", ""],
        length_function=len,
    )
    txt_chunks = text_splitter.split_documents(documentos)
    txt_chunks = [doc for doc in txt_chunks if len(doc.page_content.strip()) > 10]

    for index, chunk in enumerate(txt_chunks):
        chunk.metadata = {"id": index, "source": str(cv_file.name)}

    return txt_chunks


def _reset_directory(directory: Path) -> None:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


if __name__ == "__main__":
    rebuild_vectorstore()
