from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="mae-api-tests-"))
os.environ["PERSISTENCE_BACKEND"] = "sqlite"
os.environ["DATABASE_PATH"] = str(_TEST_ROOT / "app.db")
os.environ["STORAGE_DIR"] = str(_TEST_ROOT / "storage")
os.environ["AUTH_MODE"] = "jwt"
os.environ["JWT_SECRET"] = "test-secret-for-unit-tests"
os.environ["ENVIRONMENT"] = "test"
os.environ["CORS_ALLOW_ORIGINS"] = "http://test.local"
os.environ["MONGODB_URI"] = ""
os.environ["OPENAI_API_KEY"] = "test-openai-key-not-used"
os.environ["TERMS_OF_SERVICE_VERSION"] = "tos_v1"
os.environ["PRIVACY_POLICY_VERSION"] = "privacy_v1"

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from main import app

    return TestClient(app)


@pytest.fixture
def register_payload() -> dict[str, object]:
    return {
        "display_name": "Adriano Lima",
        "email": "adriano.legal@example.com",
        "password": "senha-forte-123",
        "terms_accepted": True,
        "privacy_accepted": True,
    }
