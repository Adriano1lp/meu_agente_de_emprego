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
    monkeypatch.setattr("config.DATABASE_PATH", db_path)
    monkeypatch.setattr("config.PERSISTENCE_BACKEND", "sqlite")
    monkeypatch.setattr("database.repository.DATABASE_PATH", db_path)
    monkeypatch.setattr("database.repository.PERSISTENCE_BACKEND", "sqlite")
    from database.repository import initialize_database

    initialize_database(db_path)
    return db_path
