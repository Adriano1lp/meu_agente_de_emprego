from __future__ import annotations

import pytest
from fastapi import HTTPException

from config import CURRENT_PRIVACY_VERSION, CURRENT_TERMS_VERSION
from database.repository import list_consent_log
from services.auth_users import (
    accept_consent_for_user,
    get_outdated_consent_code,
    register_user,
)
from services.legal import get_legal_markdown


def _register(**overrides):
    payload = {
        "display_name": "Ada Lovelace",
        "email": "ada@example.com",
        "password": "senha-forte-123",
        "terms_accepted": True,
        "terms_version": CURRENT_TERMS_VERSION,
        "privacy_accepted": True,
        "privacy_version": CURRENT_PRIVACY_VERSION,
    }
    payload.update(overrides)
    return register_user(**payload)


def test_register_requires_four_consent_fields(isolated_db):
    with pytest.raises(HTTPException) as exc:
        _register(terms_accepted=None, terms_version=None)
    assert exc.value.status_code == 400
    assert list_consent_log("missing") == []


def test_register_rejects_outdated_versions_without_creating_user(isolated_db):
    with pytest.raises(HTTPException) as exc:
        _register(terms_version="0.9")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        _register(privacy_version="0.9")
    assert exc.value.status_code == 400

    from database.repository import get_user_by_email

    assert get_user_by_email("ada@example.com") is None


def test_register_writes_two_append_only_consent_rows(isolated_db):
    user = _register()
    rows = list_consent_log(user["user_id"])
    assert [(row["doc"], row["version"]) for row in rows] == [
        ("terms", CURRENT_TERMS_VERSION),
        ("privacy", CURRENT_PRIVACY_VERSION),
    ]
    assert user["terms_version"] == CURRENT_TERMS_VERSION
    assert user["privacy_version"] == CURRENT_PRIVACY_VERSION
    assert get_outdated_consent_code(user["user_id"]) is None


def test_outdated_terms_and_privacy_codes(isolated_db):
    user = _register()
    from database.repository import update_user_consent

    update_user_consent(
        user["user_id"],
        doc="terms",
        version="0.9",
        accepted_at="2020-01-01T00:00:00+00:00",
    )
    assert get_outdated_consent_code(user["user_id"]) == "TERMS_OUTDATED"

    accept_consent_for_user(
        user["user_id"],
        doc="terms",
        version=CURRENT_TERMS_VERSION,
    )
    update_user_consent(
        user["user_id"],
        doc="privacy",
        version="0.9",
        accepted_at="2020-01-01T00:00:00+00:00",
    )
    assert get_outdated_consent_code(user["user_id"]) == "PRIVACY_OUTDATED"


def test_post_consent_appends_and_updates_user(isolated_db):
    user = _register()
    from database.repository import update_user_consent

    update_user_consent(
        user["user_id"],
        doc="terms",
        version="0.9",
        accepted_at="2020-01-01T00:00:00+00:00",
    )
    updated = accept_consent_for_user(
        user["user_id"],
        doc="terms",
        version=CURRENT_TERMS_VERSION,
    )
    assert updated["terms_version"] == CURRENT_TERMS_VERSION
    rows = list_consent_log(user["user_id"])
    assert len(rows) == 3
    assert rows[-1]["doc"] == "terms"
    assert rows[-1]["version"] == CURRENT_TERMS_VERSION


def test_consent_rejects_non_current_version(isolated_db):
    user = _register()
    with pytest.raises(HTTPException) as exc:
        accept_consent_for_user(user["user_id"], doc="privacy", version="9.9")
    assert exc.value.status_code == 400


def test_legal_docs_v1_and_unknown_version():
    terms = get_legal_markdown("terms", "1.0")
    privacy = get_legal_markdown("privacy", "1.0")
    assert "Termos de Uso" in terms
    assert "Politica de Privacidade" in privacy

    with pytest.raises(HTTPException) as exc:
        get_legal_markdown("terms", "9.9")
    assert exc.value.status_code == 404
