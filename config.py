from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
LEGACY_DOCUMENTS_DIR = BASE_DIR / "documents"
LEGACY_OUTPUT_DIR = BASE_DIR / "outputs"
LEGACY_CHROMA_DB_DIR = BASE_DIR / "chroma_cv_db"
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", str(BASE_DIR / "storage"))).resolve()
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(STORAGE_DIR / "app.db"))).resolve()
USERS_DIR = STORAGE_DIR / "users"
DEFAULT_USER_ID = os.getenv("DEFAULT_USER_ID", "default").strip() or "default"
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "analista_de_vagas").strip()
PERSISTENCE_BACKEND = os.getenv(
    "PERSISTENCE_BACKEND",
    "mongodb" if MONGODB_URI else "sqlite",
).strip().lower()

DOCUMENTS_DIR = LEGACY_DOCUMENTS_DIR
OUTPUT_DIR = LEGACY_OUTPUT_DIR
CHROMA_DB_DIR = LEGACY_CHROMA_DB_DIR
CV_FILE = DOCUMENTS_DIR / "cv.txt"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o").strip()
OPENAI_EMBEDDING_MODEL = os.getenv(
    "OPENAI_EMBEDDING_MODEL",
    "text-embedding-3-small",
).strip()

APP_NAME = os.getenv("APP_NAME", "Analista de Vagas API").strip()
APP_VERSION = os.getenv("APP_VERSION", "1.0.0").strip()
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower() or "development"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
AUTH_MODE = os.getenv("AUTH_MODE", "jwt").strip().lower() or "jwt"
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me").strip()
JWT_EXPIRATION_MINUTES = int(os.getenv("JWT_EXPIRATION_MINUTES", "10080"))
PASSWORD_RESET_EXPIRATION_MINUTES = int(
    os.getenv("PASSWORD_RESET_EXPIRATION_MINUTES", "30")
)
PASSWORD_RESET_EXPOSE_TOKEN = (
    os.getenv("PASSWORD_RESET_EXPOSE_TOKEN", "").strip().lower()
    in {"1", "true", "yes", "on"}
)
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))
CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
    if origin.strip()
]
AUTH_DIR = STORAGE_DIR / "auth"
AUTH_USERS_FILE = AUTH_DIR / "users.json"

TERMS_OF_SERVICE_VERSION = os.getenv("TERMS_OF_SERVICE_VERSION", "tos_v1").strip() or "tos_v1"
PRIVACY_POLICY_VERSION = os.getenv("PRIVACY_POLICY_VERSION", "privacy_v1").strip() or "privacy_v1"
LEGAL_DOCUMENT_TERMS = "terms_of_service"
LEGAL_DOCUMENT_PRIVACY = "privacy_policy"

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_PRICE_ESSENCIAL = os.getenv("STRIPE_PRICE_ESSENCIAL", "").strip()
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "").strip()
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "").strip()
PLAN_FREE = "free"
PLAN_ESSENCIAL = "essencial"
FREE_LLM_QUOTA_MONTHLY = int(os.getenv("FREE_LLM_QUOTA_MONTHLY", "5"))
ESSENCIAL_LLM_QUOTA_MONTHLY = int(os.getenv("ESSENCIAL_LLM_QUOTA_MONTHLY", "100"))
STRIPE_WEBHOOK_TOLERANCE_SECONDS = int(os.getenv("STRIPE_WEBHOOK_TOLERANCE_SECONDS", "300"))

OBJECT_STORAGE_BACKEND = os.getenv("OBJECT_STORAGE_BACKEND", "local").strip().lower() or "local"
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "").strip()
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID", "").strip()
S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY", "").strip()
S3_BUCKET = os.getenv("S3_BUCKET", "").strip()
S3_REGION = os.getenv("S3_REGION", "auto").strip() or "auto"
S3_SIGNED_URL_EXPIRES = int(os.getenv("S3_SIGNED_URL_EXPIRES", "3600"))

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
USERS_DIR.mkdir(parents=True, exist_ok=True)
AUTH_DIR.mkdir(parents=True, exist_ok=True)


def sanitize_user_id(user_id: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", user_id.strip())
    normalized = normalized.strip("._-")
    if not normalized:
        raise ValueError("user_id invalido")
    return normalized


def get_user_base_dir(user_id: str) -> Path:
    user_dir = USERS_DIR / sanitize_user_id(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_user_documents_dir(user_id: str) -> Path:
    documents_dir = get_user_base_dir(user_id) / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    return documents_dir


def get_user_cv_file(user_id: str) -> Path:
    return get_user_documents_dir(user_id) / "cv.txt"


def get_user_profile_file(user_id: str) -> Path:
    return get_user_base_dir(user_id) / "profile.json"


def get_user_profile_versions_file(user_id: str) -> Path:
    return get_user_base_dir(user_id) / "profile_versions.jsonl"


def get_user_chroma_dir(user_id: str) -> Path:
    chroma_dir = get_user_base_dir(user_id) / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    return chroma_dir


def get_user_output_dir(user_id: str) -> Path:
    output_dir = get_user_base_dir(user_id) / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_default_user_base_dir() -> Path:
    return get_user_base_dir(DEFAULT_USER_ID)


def get_default_user_cv_file() -> Path:
    return get_user_cv_file(DEFAULT_USER_ID)


def get_default_user_chroma_dir() -> Path:
    return get_user_chroma_dir(DEFAULT_USER_ID)


def get_default_user_output_dir() -> Path:
    return get_user_output_dir(DEFAULT_USER_ID)


def ensure_openai_api_key() -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY nao configurada. Defina a variavel de ambiente "
            "antes de iniciar a API."
        )
    return OPENAI_API_KEY


def ensure_runtime_config() -> None:
    is_production = ENVIRONMENT in {"production", "prod"}

    if not is_production:
        return

    if AUTH_MODE == "jwt" and JWT_SECRET == "dev-secret-change-me":
        raise RuntimeError(
            "JWT_SECRET insegura em producao. Defina uma chave forte antes de iniciar a API."
        )

    if not CORS_ALLOW_ORIGINS or CORS_ALLOW_ORIGINS == ["*"]:
        raise RuntimeError(
            "CORS_ALLOW_ORIGINS nao pode ser '*' em producao. "
            "Defina explicitamente o dominio do app."
        )

    if PERSISTENCE_BACKEND != "mongodb" or not MONGODB_URI:
        raise RuntimeError(
            "MongoDB obrigatorio em producao. Defina MONGODB_URI e "
            "MONGODB_DATABASE no Render para manter usuarios e embeddings persistentes."
        )
