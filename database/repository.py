from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from config import DATABASE_PATH, PERSISTENCE_BACKEND
from database import mongo_repository

API_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = API_DIR / "database" / "schema.sql"


def initialize_database(database_path: Path | None = None) -> Path:
    if _use_mongodb():
        mongo_repository.initialize_database()
        return (database_path or DATABASE_PATH).resolve()

    database_path = (database_path or DATABASE_PATH).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema)
        _ensure_legal_schema(connection)
        _ensure_billing_schema(connection)
        _ensure_storage_schema(connection)
        connection.commit()

    return database_path


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    initialize_database()
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_user(user: dict[str, Any]) -> None:
    if _use_mongodb():
        mongo_repository.create_user(user)
        return

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO users (
                user_id, email, display_name, password_hash, terms_accepted,
                terms_accepted_at, terms_version, privacy_accepted,
                privacy_accepted_at, privacy_version, created_at, updated_at
            )
            VALUES (
                :user_id, :email, :display_name, :password_hash, :terms_accepted,
                :terms_accepted_at, :terms_version, :privacy_accepted,
                :privacy_accepted_at, :privacy_version, :created_at, :updated_at
            )
            """,
            user,
        )


def ensure_user_exists(user_id: str) -> None:
    if _use_mongodb():
        mongo_repository.ensure_user_exists(user_id)
        return

    with _connect() as connection:
        row = connection.execute(
            "SELECT user_id FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row:
            return

        connection.execute(
            """
            INSERT INTO users (user_id, email, display_name, password_hash, terms_accepted)
            VALUES (?, ?, ?, ?, 0)
            """,
            (
                user_id,
                f"{user_id}@local.invalid",
                user_id,
                "legacy_external_auth",
            ),
        )


def get_user_by_email(email: str) -> dict[str, Any] | None:
    if _use_mongodb():
        return mongo_repository.get_user_by_email(email)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT user_id, email, display_name, password_hash, terms_accepted,
                terms_accepted_at, terms_version, privacy_accepted,
                privacy_accepted_at, privacy_version, created_at, updated_at
            FROM users
            WHERE email = ? AND deleted_at IS NULL
            """,
            (email,),
        ).fetchone()
        return _row_to_dict(row)


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    if _use_mongodb():
        return mongo_repository.get_user_by_id(user_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT user_id, email, display_name, password_hash, terms_accepted,
                terms_accepted_at, terms_version, privacy_accepted,
                privacy_accepted_at, privacy_version, created_at, updated_at
            FROM users
            WHERE user_id = ? AND deleted_at IS NULL
            """,
            (user_id,),
        ).fetchone()
        return _row_to_dict(row)


def accept_user_terms(user_id: str, accepted_at: str) -> dict[str, Any] | None:
    return accept_user_legal_documents(
        user_id,
        accepted_at=accepted_at,
        terms_version=None,
        privacy_version=None,
    )


def accept_user_legal_documents(
    user_id: str,
    *,
    accepted_at: str,
    terms_version: str | None,
    privacy_version: str | None,
) -> dict[str, Any] | None:
    if _use_mongodb():
        return mongo_repository.accept_user_legal_documents(
            user_id,
            accepted_at=accepted_at,
            terms_version=terms_version,
            privacy_version=privacy_version,
        )

    with _connect() as connection:
        connection.execute(
            """
            UPDATE users
            SET terms_accepted = 1,
                terms_accepted_at = ?,
                terms_version = COALESCE(?, terms_version),
                privacy_accepted = 1,
                privacy_accepted_at = ?,
                privacy_version = COALESCE(?, privacy_version),
                updated_at = ?
            WHERE user_id = ? AND deleted_at IS NULL
            """,
            (
                accepted_at,
                terms_version,
                accepted_at,
                privacy_version,
                accepted_at,
                user_id,
            ),
        )
        row = connection.execute(
            """
            SELECT user_id, email, display_name, password_hash, terms_accepted,
                terms_accepted_at, terms_version, privacy_accepted,
                privacy_accepted_at, privacy_version, created_at, updated_at
            FROM users
            WHERE user_id = ? AND deleted_at IS NULL
            """,
            (user_id,),
        ).fetchone()
        return _row_to_dict(row)


def append_consent_log(entry: dict[str, Any]) -> None:
    if _use_mongodb():
        mongo_repository.append_consent_log(entry)
        return

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO consent_log (
                user_id, document_type, document_version, accepted, accepted_at,
                source, ip_address, user_agent
            )
            VALUES (
                :user_id, :document_type, :document_version, :accepted, :accepted_at,
                :source, :ip_address, :user_agent
            )
            """,
            {
                "user_id": entry["user_id"],
                "document_type": entry["document_type"],
                "document_version": entry["document_version"],
                "accepted": 1 if entry.get("accepted", True) else 0,
                "accepted_at": entry["accepted_at"],
                "source": entry.get("source") or "unknown",
                "ip_address": entry.get("ip_address"),
                "user_agent": entry.get("user_agent"),
            },
        )


def list_consent_log(user_id: str) -> list[dict[str, Any]]:
    if _use_mongodb():
        return mongo_repository.list_consent_log(user_id)

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT consent_id, user_id, document_type, document_version, accepted,
                accepted_at, source, ip_address, user_agent
            FROM consent_log
            WHERE user_id = ?
            ORDER BY accepted_at ASC, consent_id ASC
            """,
            (user_id,),
        ).fetchall()
    return [_consent_row_to_dict(row) for row in rows]


def collect_user_export_payload(user_id: str) -> dict[str, Any]:
    if _use_mongodb():
        return mongo_repository.collect_user_export_payload(user_id)
    return _sqlite_collect_user_export_payload(user_id)


def get_user_subscription(user_id: str) -> dict[str, Any] | None:
    if _use_mongodb():
        return mongo_repository.get_user_subscription(user_id)
    return _sqlite_get_user_subscription(user_id)


def upsert_user_subscription(subscription: dict[str, Any]) -> dict[str, Any]:
    if _use_mongodb():
        return mongo_repository.upsert_user_subscription(subscription)
    return _sqlite_upsert_user_subscription(subscription)


def get_subscription_by_stripe_id(stripe_subscription_id: str) -> dict[str, Any] | None:
    if _use_mongodb():
        return mongo_repository.get_subscription_by_stripe_id(stripe_subscription_id)
    return _sqlite_get_subscription_by_stripe_id(stripe_subscription_id)


def append_usage_event(event: dict[str, Any]) -> None:
    if _use_mongodb():
        mongo_repository.append_usage_event(event)
        return
    _sqlite_append_usage_event(event)


def count_usage_units(user_id: str, period: str) -> int:
    if _use_mongodb():
        return mongo_repository.count_usage_units(user_id, period)
    return _sqlite_count_usage_units(user_id, period)


def list_usage_events(user_id: str) -> list[dict[str, Any]]:
    if _use_mongodb():
        return mongo_repository.list_usage_events(user_id)
    return _sqlite_list_usage(user_id)


def anonymize_and_purge_user(user_id: str, *, deleted_at: str) -> bool:
    if _use_mongodb():
        return mongo_repository.anonymize_and_purge_user(
            user_id,
            deleted_at=deleted_at,
        )
    return _sqlite_anonymize_and_purge_user(user_id, deleted_at=deleted_at)


def update_user_password_hash(
    *,
    user_id: str,
    password_hash: str,
    updated_at: str,
) -> dict[str, Any] | None:
    if _use_mongodb():
        return mongo_repository.update_user_password_hash(
            user_id=user_id,
            password_hash=password_hash,
            updated_at=updated_at,
        )

    with _connect() as connection:
        connection.execute(
            """
            UPDATE users
            SET password_hash = ?, updated_at = ?
            WHERE user_id = ? AND deleted_at IS NULL
            """,
            (password_hash, updated_at, user_id),
        )
        row = connection.execute(
            """
            SELECT user_id, email, display_name, password_hash, terms_accepted,
                terms_accepted_at, terms_version, privacy_accepted,
                privacy_accepted_at, privacy_version, created_at, updated_at
            FROM users
            WHERE user_id = ? AND deleted_at IS NULL
            """,
            (user_id,),
        ).fetchone()
        return _row_to_dict(row)


def create_password_reset_token(token_data: dict[str, Any]) -> None:
    if _use_mongodb():
        mongo_repository.create_password_reset_token(token_data)
        return

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO password_reset_tokens (
                user_id, email, token_hash, expires_at, created_at, used_at
            )
            VALUES (
                :user_id, :email, :token_hash, :expires_at, :created_at, :used_at
            )
            """,
            token_data,
        )


def get_password_reset_token_by_hash(token_hash: str) -> dict[str, Any] | None:
    if _use_mongodb():
        return mongo_repository.get_password_reset_token_by_hash(token_hash)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT reset_token_id, user_id, email, token_hash, expires_at,
                created_at, used_at
            FROM password_reset_tokens
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        return _row_to_dict(row)


def mark_password_reset_token_used(*, token_hash: str, used_at: str) -> None:
    if _use_mongodb():
        mongo_repository.mark_password_reset_token_used(
            token_hash=token_hash,
            used_at=used_at,
        )
        return

    with _connect() as connection:
        connection.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE token_hash = ? AND used_at IS NULL
            """,
            (used_at, token_hash),
        )

def upsert_user_profile(user_id: str, version: int, profile_data: dict[str, Any]) -> None:
    if _use_mongodb():
        mongo_repository.upsert_user_profile(user_id, version, profile_data)
        return

    ensure_user_exists(user_id)
    profile_json = json.dumps(profile_data, ensure_ascii=True)
    fields = {
        "user_id": user_id,
        "version": version,
        "nome_completo": profile_data.get("nome_completo"),
        "email": profile_data.get("email"),
        "telefone": profile_data.get("telefone"),
        "linkedin": profile_data.get("linkedin"),
        "resumo_profissional": profile_data.get("resumo_profissional"),
        "profile_json": profile_json,
    }
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO user_profiles (
                user_id, version, nome_completo, email, telefone, linkedin,
                resumo_profissional, profile_json
            )
            VALUES (
                :user_id, :version, :nome_completo, :email, :telefone, :linkedin,
                :resumo_profissional, :profile_json
            )
            ON CONFLICT(user_id) DO UPDATE SET
                version = excluded.version,
                nome_completo = excluded.nome_completo,
                email = excluded.email,
                telefone = excluded.telefone,
                linkedin = excluded.linkedin,
                resumo_profissional = excluded.resumo_profissional,
                profile_json = excluded.profile_json,
                updated_at = datetime('now')
            """,
            fields,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO user_profile_versions (user_id, version, profile_json)
            VALUES (?, ?, ?)
            """,
            (user_id, version, profile_json),
        )


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    if _use_mongodb():
        return mongo_repository.get_user_profile(user_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT user_id, version, profile_json, updated_at
            FROM user_profiles
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    if not row:
        return None

    return {
        "user_id": row["user_id"],
        "version": row["version"],
        "updated_at": row["updated_at"],
        "profile": json.loads(row["profile_json"]),
    }


def create_user_document(document: dict[str, Any]) -> int | str:
    if _use_mongodb():
        return mongo_repository.create_user_document(document)

    ensure_user_exists(document["user_id"])
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO user_documents (
                user_id, document_type, original_filename, original_content_type,
                original_file_path, extracted_text_path, bytes_received, checksum_sha256
            )
            VALUES (
                :user_id, :document_type, :original_filename, :original_content_type,
                :original_file_path, :extracted_text_path, :bytes_received, :checksum_sha256
            )
            """,
            document,
        )
        return int(cursor.lastrowid)


def get_latest_user_document_id(user_id: str) -> int | str | None:
    if _use_mongodb():
        return mongo_repository.get_latest_user_document_id(user_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT document_id
            FROM user_documents
            WHERE user_id = ? AND document_type = 'cv'
            ORDER BY created_at DESC, document_id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        return int(row["document_id"]) if row else None


def get_latest_user_cv_text(user_id: str) -> str | None:
    if _use_mongodb():
        return mongo_repository.get_latest_user_cv_text(user_id)

    return None


def create_embedding_run(run: dict[str, Any]) -> int | str:
    if _use_mongodb():
        return mongo_repository.create_embedding_run(run)

    ensure_user_exists(run["user_id"])
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO embedding_runs (
                user_id, document_id, embedding_model, chunks, chroma_dir,
                cv_file_path, status, error_message, processed_at
            )
            VALUES (
                :user_id, :document_id, :embedding_model, :chunks, :chroma_dir,
                :cv_file_path, :status, :error_message, :processed_at
            )
            """,
            run,
        )
        return int(cursor.lastrowid)


def replace_embedding_chunks(
    *,
    user_id: str,
    embedding_run_id: str,
    embedding_model: str,
    chunks: list[dict[str, Any]],
) -> None:
    if _use_mongodb():
        mongo_repository.replace_embedding_chunks(
            user_id=user_id,
            embedding_run_id=embedding_run_id,
            embedding_model=embedding_model,
            chunks=chunks,
        )
        return


def count_embedding_chunks(user_id: str) -> int:
    if _use_mongodb():
        return mongo_repository.count_embedding_chunks(user_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT chunks
            FROM embedding_runs
            WHERE user_id = ? AND status = 'completed'
            ORDER BY processed_at DESC, embedding_run_id DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        return int(row["chunks"]) if row else 0


def find_similar_embedding_chunks(
    *,
    user_id: str,
    query_embedding: list[float],
    limit: int = 6,
) -> list[dict[str, Any]]:
    if _use_mongodb():
        return mongo_repository.find_similar_embedding_chunks(
            user_id=user_id,
            query_embedding=query_embedding,
            limit=limit,
        )

    return []


def create_processing_run(run: dict[str, Any]) -> int | str:
    if _use_mongodb():
        return mongo_repository.create_processing_run(run)

    ensure_user_exists(run["user_id"])
    payload = {
        **run,
        "job_data_json": _json_or_none(run.get("job_data")),
        "matching_json": _json_or_none(run.get("matching")),
        "optimization_json": _json_or_none(run.get("optimization")),
    }
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO processing_runs (
                user_id, input_text, job_data_json, matching_json, optimization_json,
                response_text, status, error_message, completed_at
            )
            VALUES (
                :user_id, :input_text, :job_data_json, :matching_json, :optimization_json,
                :response_text, :status, :error_message, :completed_at
            )
            """,
            payload,
        )
        return int(cursor.lastrowid)


def create_job_analysis_insight(insight: dict[str, Any]) -> int | str:
    if _use_mongodb():
        return mongo_repository.create_job_analysis_insight(insight)

    ensure_user_exists(insight["user_id"])
    payload = {
        **insight,
        "strengths_json": _json_or_none(insight.get("strengths")) or "[]",
        "critical_gaps_json": _json_or_none(insight.get("critical_gaps")) or "[]",
        "matching_skills_json": _json_or_none(insight.get("matching_skills")) or "[]",
        "missing_skills_json": _json_or_none(insight.get("missing_skills")) or "[]",
        "generation_blocked": 1 if insight.get("generation_blocked") else 0,
    }
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO job_analysis_insights (
                user_id, processing_run_id, job_title, company_name, job_summary,
                match_score, strengths_json, critical_gaps_json, matching_skills_json,
                missing_skills_json, status, generation_blocked, blocked_reason, source,
                created_at
            )
            VALUES (
                :user_id, :processing_run_id, :job_title, :company_name, :job_summary,
                :match_score, :strengths_json, :critical_gaps_json, :matching_skills_json,
                :missing_skills_json, :status, :generation_blocked, :blocked_reason,
                :source, :created_at
            )
            ON CONFLICT(user_id, processing_run_id) DO UPDATE SET
                job_title = excluded.job_title,
                company_name = excluded.company_name,
                job_summary = excluded.job_summary,
                match_score = excluded.match_score,
                strengths_json = excluded.strengths_json,
                critical_gaps_json = excluded.critical_gaps_json,
                matching_skills_json = excluded.matching_skills_json,
                missing_skills_json = excluded.missing_skills_json,
                status = excluded.status,
                generation_blocked = excluded.generation_blocked,
                blocked_reason = excluded.blocked_reason,
                source = excluded.source
            """,
            payload,
        )
        return int(cursor.lastrowid)


def list_job_analysis_insights(
    user_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if _use_mongodb():
        return mongo_repository.list_job_analysis_insights(
            user_id,
            limit=limit,
            offset=offset,
        )

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT
                insight_id, user_id, processing_run_id, job_title, company_name,
                job_summary, match_score, strengths_json, critical_gaps_json,
                matching_skills_json, missing_skills_json, status, generation_blocked,
                blocked_reason, source, created_at
            FROM job_analysis_insights
            WHERE user_id = ?
            ORDER BY created_at DESC, insight_id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()

    return [_insight_row_to_dict(row) for row in rows]


def create_generated_file(file_data: dict[str, Any]) -> int | str:
    if _use_mongodb():
        return mongo_repository.create_generated_file(file_data)

    ensure_user_exists(file_data["user_id"])
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO generated_files (
                user_id, processing_run_id, file_name, file_path, object_key,
                public_url, media_type, bytes_size
            )
            VALUES (
                :user_id, :processing_run_id, :file_name, :file_path, :object_key,
                :public_url, :media_type, :bytes_size
            )
            """,
            {**file_data, "object_key": file_data.get("object_key")},
        )
        return int(cursor.lastrowid)


def create_development_plan(plan: dict[str, Any]) -> str:
    if _use_mongodb():
        return mongo_repository.create_development_plan(plan)

    ensure_user_exists(plan["user_id"])
    payload = _development_plan_to_sql_payload(plan)
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO development_plans (
                pdi_id, user_id, source_insight_ids_json,
                source_processing_run_ids_json, generated_from_limit, title,
                main_objective, summary, secondary_objectives_json,
                priority_areas_json, priority_gaps_json, strengths_to_leverage_json,
                plan_70_json, plan_20_json, plan_10_json, checklist_items_json,
                progress_percent, status, created_at, updated_at, completed_at
            )
            VALUES (
                :pdi_id, :user_id, :source_insight_ids_json,
                :source_processing_run_ids_json, :generated_from_limit, :title,
                :main_objective, :summary, :secondary_objectives_json,
                :priority_areas_json, :priority_gaps_json, :strengths_to_leverage_json,
                :plan_70_json, :plan_20_json, :plan_10_json, :checklist_items_json,
                :progress_percent, :status, :created_at, :updated_at, :completed_at
            )
            """,
            payload,
        )
    return str(plan["pdi_id"])


def update_development_plan(plan: dict[str, Any]) -> None:
    if _use_mongodb():
        mongo_repository.update_development_plan(plan)
        return

    payload = _development_plan_to_sql_payload(plan)
    with _connect() as connection:
        connection.execute(
            """
            UPDATE development_plans
            SET
                source_insight_ids_json = :source_insight_ids_json,
                source_processing_run_ids_json = :source_processing_run_ids_json,
                generated_from_limit = :generated_from_limit,
                title = :title,
                main_objective = :main_objective,
                summary = :summary,
                secondary_objectives_json = :secondary_objectives_json,
                priority_areas_json = :priority_areas_json,
                priority_gaps_json = :priority_gaps_json,
                strengths_to_leverage_json = :strengths_to_leverage_json,
                plan_70_json = :plan_70_json,
                plan_20_json = :plan_20_json,
                plan_10_json = :plan_10_json,
                checklist_items_json = :checklist_items_json,
                progress_percent = :progress_percent,
                status = :status,
                updated_at = :updated_at,
                completed_at = :completed_at
            WHERE pdi_id = :pdi_id AND user_id = :user_id
            """,
            payload,
        )


def get_active_development_plan(user_id: str) -> dict[str, Any] | None:
    if _use_mongodb():
        return mongo_repository.get_active_development_plan(user_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM development_plans
            WHERE user_id = ? AND status = 'active'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return _development_plan_row_to_dict(row) if row else None


def get_development_plan(user_id: str, pdi_id: str) -> dict[str, Any] | None:
    if _use_mongodb():
        return mongo_repository.get_development_plan(user_id=user_id, pdi_id=pdi_id)

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM development_plans
            WHERE user_id = ? AND pdi_id = ?
            """,
            (user_id, pdi_id),
        ).fetchone()
    return _development_plan_row_to_dict(row) if row else None


def list_development_plans(
    user_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if _use_mongodb():
        return mongo_repository.list_development_plans(
            user_id,
            limit=limit,
            offset=offset,
        )

    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM development_plans
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()
    return [_development_plan_row_to_dict(row) for row in rows]


def count_generated_files(user_id: str) -> int:
    if _use_mongodb():
        return mongo_repository.count_generated_files(user_id)

    with _connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM generated_files WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return int(row["total"]) if row else 0


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _ensure_legal_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in connection.execute("PRAGMA table_info(users)").fetchall()
    }
    column_ddl = {
        "terms_accepted": "INTEGER NOT NULL DEFAULT 0",
        "terms_accepted_at": "TEXT",
        "terms_version": "TEXT",
        "privacy_accepted": "INTEGER NOT NULL DEFAULT 0",
        "privacy_accepted_at": "TEXT",
        "privacy_version": "TEXT",
    }
    for column_name, definition in column_ddl.items():
        if column_name not in columns:
            connection.execute(
                f"ALTER TABLE users ADD COLUMN {column_name} {definition}",
            )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS consent_log (
            consent_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            document_type TEXT NOT NULL,
            document_version TEXT NOT NULL,
            accepted INTEGER NOT NULL DEFAULT 1,
            accepted_at TEXT NOT NULL,
            source TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_consent_log_user_id
            ON consent_log (user_id, accepted_at)
        """
    )


def _consent_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "consent_id": row["consent_id"],
        "user_id": row["user_id"],
        "document_type": row["document_type"],
        "document_version": row["document_version"],
        "accepted": bool(row["accepted"]),
        "accepted_at": row["accepted_at"],
        "source": row["source"],
        "ip_address": row["ip_address"],
        "user_agent": row["user_agent"],
    }


def _sqlite_collect_user_export_payload(user_id: str) -> dict[str, Any]:
    with _connect() as connection:
        user_row = connection.execute(
            """
            SELECT user_id, email, display_name, terms_accepted, terms_accepted_at,
                terms_version, privacy_accepted, privacy_accepted_at, privacy_version,
                created_at, updated_at
            FROM users
            WHERE user_id = ? AND deleted_at IS NULL
            """,
            (user_id,),
        ).fetchone()
        if not user_row:
            return {}

        profile_row = connection.execute(
            """
            SELECT user_id, version, profile_json, updated_at
            FROM user_profiles
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        version_rows = connection.execute(
            """
            SELECT version, profile_json, created_at
            FROM user_profile_versions
            WHERE user_id = ?
            ORDER BY version ASC
            """,
            (user_id,),
        ).fetchall()
        document_rows = connection.execute(
            """
            SELECT document_id, document_type, original_filename, original_content_type,
                original_file_path, extracted_text_path, bytes_received, checksum_sha256,
                created_at
            FROM user_documents
            WHERE user_id = ?
            ORDER BY created_at ASC
            """,
            (user_id,),
        ).fetchall()
        processing_rows = connection.execute(
            """
            SELECT processing_run_id, input_text, job_data_json, matching_json,
                optimization_json, response_text, status, error_message, created_at,
                completed_at
            FROM processing_runs
            WHERE user_id = ?
            ORDER BY created_at ASC
            """,
            (user_id,),
        ).fetchall()
        file_rows = connection.execute(
            """
            SELECT generated_file_id, file_name, file_path, object_key, public_url,
                media_type, bytes_size, created_at
            FROM generated_files
            WHERE user_id = ?
            ORDER BY created_at ASC
            """,
            (user_id,),
        ).fetchall()

    return {
        "user": dict(user_row),
        "consent_log": list_consent_log(user_id),
        "profile": {
            "user_id": profile_row["user_id"],
            "version": profile_row["version"],
            "updated_at": profile_row["updated_at"],
            "profile": json.loads(profile_row["profile_json"]),
        } if profile_row else None,
        "profile_versions": [
            {
                "version": row["version"],
                "created_at": row["created_at"],
                "profile": json.loads(row["profile_json"]),
            }
            for row in version_rows
        ],
        "documents": [
            {
                **dict(row),
                "extracted_text": _read_text_file(row["extracted_text_path"]),
            }
            for row in document_rows
        ],
        "processing_runs": [
            {
                "processing_run_id": row["processing_run_id"],
                "input_text": row["input_text"],
                "job_data": _json_load_or_none(row["job_data_json"]),
                "matching": _json_load_or_none(row["matching_json"]),
                "optimization": _json_load_or_none(row["optimization_json"]),
                "response_text": row["response_text"],
                "status": row["status"],
                "error_message": row["error_message"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            }
            for row in processing_rows
        ],
        "job_analysis_insights": list_job_analysis_insights(
            user_id,
            limit=1000,
            offset=0,
        ),
        "development_plans": list_development_plans(user_id, limit=1000, offset=0),
        "generated_files": [dict(row) for row in file_rows],
        "subscription": _sqlite_get_user_subscription(user_id),
        "usage": _sqlite_list_usage(user_id),
    }


def _sqlite_anonymize_and_purge_user(user_id: str, *, deleted_at: str) -> bool:
    with _connect() as connection:
        user_row = connection.execute(
            "SELECT user_id FROM users WHERE user_id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone()
        if not user_row:
            return False

        for table_name in (
            "generated_files",
            "job_analysis_insights",
            "development_plans",
            "processing_runs",
            "embedding_runs",
            "user_documents",
            "user_profile_versions",
            "user_profiles",
            "password_reset_tokens",
            "usage_ledger",
            "user_subscriptions",
        ):
            connection.execute(
                f"DELETE FROM {table_name} WHERE user_id = ?",
                (user_id,),
            )

        connection.execute(
            """
            UPDATE users
            SET email = ?,
                display_name = ?,
                password_hash = ?,
                updated_at = ?,
                deleted_at = ?
            WHERE user_id = ?
            """,
            (
                f"deleted_{user_id}@deleted.invalid",
                "Conta excluida",
                "deleted_account",
                deleted_at,
                deleted_at,
                user_id,
            ),
        )
    return True


def _read_text_file(path_value: str | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _json_load_or_none(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)


def _ensure_storage_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in connection.execute("PRAGMA table_info(generated_files)").fetchall()
    }
    if columns and "object_key" not in columns:
        connection.execute("ALTER TABLE generated_files ADD COLUMN object_key TEXT")


def _ensure_billing_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_subscriptions (
            user_id TEXT PRIMARY KEY,
            plan TEXT NOT NULL DEFAULT 'free',
            status TEXT NOT NULL DEFAULT 'active',
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_price_id TEXT,
            current_period_start TEXT,
            current_period_end TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_ledger (
            usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            feature TEXT NOT NULL,
            units INTEGER NOT NULL DEFAULT 1,
            period TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_usage_ledger_user_period
            ON usage_ledger (user_id, period)
        """
    )


def _sqlite_get_subscription_by_stripe_id(
    stripe_subscription_id: str,
) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT user_id, plan, status, stripe_customer_id, stripe_subscription_id,
                stripe_price_id, current_period_start, current_period_end, updated_at
            FROM user_subscriptions
            WHERE stripe_subscription_id = ?
            """,
            (stripe_subscription_id,),
        ).fetchone()
    return dict(row) if row else None


def _sqlite_get_user_subscription(user_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT user_id, plan, status, stripe_customer_id, stripe_subscription_id,
                stripe_price_id, current_period_start, current_period_end, updated_at
            FROM user_subscriptions
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def _sqlite_upsert_user_subscription(subscription: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "user_id": subscription["user_id"],
        "plan": subscription.get("plan") or "free",
        "status": subscription.get("status") or "active",
        "stripe_customer_id": subscription.get("stripe_customer_id"),
        "stripe_subscription_id": subscription.get("stripe_subscription_id"),
        "stripe_price_id": subscription.get("stripe_price_id"),
        "current_period_start": subscription.get("current_period_start"),
        "current_period_end": subscription.get("current_period_end"),
        "updated_at": subscription.get("updated_at"),
    }
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO user_subscriptions (
                user_id, plan, status, stripe_customer_id, stripe_subscription_id,
                stripe_price_id, current_period_start, current_period_end, updated_at
            )
            VALUES (
                :user_id, :plan, :status, :stripe_customer_id, :stripe_subscription_id,
                :stripe_price_id, :current_period_start, :current_period_end, :updated_at
            )
            ON CONFLICT(user_id) DO UPDATE SET
                plan = excluded.plan,
                status = excluded.status,
                stripe_customer_id = excluded.stripe_customer_id,
                stripe_subscription_id = excluded.stripe_subscription_id,
                stripe_price_id = excluded.stripe_price_id,
                current_period_start = excluded.current_period_start,
                current_period_end = excluded.current_period_end,
                updated_at = excluded.updated_at
            """,
            payload,
        )
    return payload


def _sqlite_append_usage_event(event: dict[str, Any]) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO usage_ledger (user_id, feature, units, period, created_at)
            VALUES (:user_id, :feature, :units, :period, :created_at)
            """,
            {
                "user_id": event["user_id"],
                "feature": event["feature"],
                "units": int(event.get("units") or 1),
                "period": event["period"],
                "created_at": event["created_at"],
            },
        )


def _sqlite_count_usage_units(user_id: str, period: str) -> int:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(units), 0) AS total
            FROM usage_ledger
            WHERE user_id = ? AND period = ?
            """,
            (user_id, period),
        ).fetchone()
    return int(row["total"]) if row else 0


def _sqlite_list_usage(user_id: str) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT usage_id, user_id, feature, units, period, created_at
            FROM usage_ledger
            WHERE user_id = ?
            ORDER BY created_at ASC, usage_id ASC
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True)


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    loaded = json.loads(value)
    return loaded if isinstance(loaded, list) else []


def _insight_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["insight_id"]),
        "processing_run_id": row["processing_run_id"],
        "created_at": row["created_at"],
        "job_title": row["job_title"],
        "company_name": row["company_name"],
        "job_summary": row["job_summary"],
        "match_score": int(row["match_score"]),
        "strengths": _json_list(row["strengths_json"]),
        "critical_gaps": _json_list(row["critical_gaps_json"]),
        "matching_skills": _json_list(row["matching_skills_json"]),
        "missing_skills": _json_list(row["missing_skills_json"]),
        "status": row["status"],
        "generation_blocked": bool(row["generation_blocked"]),
        "blocked_reason": row["blocked_reason"],
        "source": row["source"],
    }


def _development_plan_to_sql_payload(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "pdi_id": plan["pdi_id"],
        "user_id": plan["user_id"],
        "source_insight_ids_json": _json_or_none(plan.get("source_insight_ids")) or "[]",
        "source_processing_run_ids_json": (
            _json_or_none(plan.get("source_processing_run_ids")) or "[]"
        ),
        "generated_from_limit": int(plan.get("generated_from_limit") or 10),
        "title": plan["title"],
        "main_objective": plan["main_objective"],
        "summary": plan["summary"],
        "secondary_objectives_json": _json_or_none(plan.get("secondary_objectives")) or "[]",
        "priority_areas_json": _json_or_none(plan.get("priority_areas")) or "[]",
        "priority_gaps_json": _json_or_none(plan.get("priority_gaps")) or "[]",
        "strengths_to_leverage_json": (
            _json_or_none(plan.get("strengths_to_leverage")) or "[]"
        ),
        "plan_70_json": _json_or_none(plan.get("plan_70")) or "[]",
        "plan_20_json": _json_or_none(plan.get("plan_20")) or "[]",
        "plan_10_json": _json_or_none(plan.get("plan_10")) or "[]",
        "checklist_items_json": _json_or_none(plan.get("checklist_items")) or "[]",
        "progress_percent": int(plan.get("progress_percent") or 0),
        "status": plan.get("status") or "active",
        "created_at": plan.get("created_at"),
        "updated_at": plan.get("updated_at"),
        "completed_at": plan.get("completed_at"),
    }


def _development_plan_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "pdi_id": row["pdi_id"],
        "user_id": row["user_id"],
        "source_insight_ids": _json_list(row["source_insight_ids_json"]),
        "source_processing_run_ids": _json_list(row["source_processing_run_ids_json"]),
        "generated_from_limit": int(row["generated_from_limit"]),
        "title": row["title"],
        "main_objective": row["main_objective"],
        "summary": row["summary"],
        "secondary_objectives": _json_list(row["secondary_objectives_json"]),
        "priority_areas": _json_list(row["priority_areas_json"]),
        "priority_gaps": _json_list(row["priority_gaps_json"]),
        "strengths_to_leverage": _json_list(row["strengths_to_leverage_json"]),
        "plan_70": _json_list(row["plan_70_json"]),
        "plan_20": _json_list(row["plan_20_json"]),
        "plan_10": _json_list(row["plan_10_json"]),
        "checklist_items": _json_list(row["checklist_items_json"]),
        "progress_percent": int(row["progress_percent"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _use_mongodb() -> bool:
    return PERSISTENCE_BACKEND == "mongodb" and mongo_repository.is_configured()
