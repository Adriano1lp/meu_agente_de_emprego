from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DOCUMENTS_DIR = BASE_DIR / "documents"
OUTPUT_DIR = BASE_DIR / "outputs"
CHROMA_DB_DIR = BASE_DIR / "chroma_cv_db"
CV_FILE = DOCUMENTS_DIR / "cv.txt"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o").strip()
OPENAI_EMBEDDING_MODEL = os.getenv(
    "OPENAI_EMBEDDING_MODEL",
    "text-embedding-3-small",
).strip()

APP_NAME = os.getenv("APP_NAME", "Analista de Vagas API").strip()
APP_VERSION = os.getenv("APP_VERSION", "1.0.0").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
    if origin.strip()
]

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def ensure_openai_api_key() -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY nao configurada. Defina a variavel de ambiente "
            "antes de iniciar a API."
        )
    return OPENAI_API_KEY
