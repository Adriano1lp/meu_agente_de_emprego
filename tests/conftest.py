from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ["PERSISTENCE_BACKEND"] = "sqlite"
os.environ["AUTH_MODE"] = "jwt"
os.environ["MONGODB_URI"] = ""


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "app.db"
    storage_dir = tmp_path / "storage"
    users_dir = storage_dir / "users"
    users_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("config.DATABASE_PATH", db_path)
    monkeypatch.setattr("config.STORAGE_DIR", storage_dir)
    monkeypatch.setattr("config.USERS_DIR", users_dir)
    monkeypatch.setattr("config.PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setattr("database.repository.DATABASE_PATH", db_path)
    monkeypatch.setattr("database.repository.PERSISTENCE_BACKEND", "sqlite")
    from database.repository import initialize_database

    initialize_database(db_path)
    return db_path
