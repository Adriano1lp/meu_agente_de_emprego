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


def initialize_database(database_path: Path = DATABASE_PATH) -> Path:
    if _use_mongodb():
        mongo_repository.initialize_database()
        return database_path.resolve()

    database_path = database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema)
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
                user_id, email, display_name, password_hash, created_at, updated_at
            )
            VALUES (:user_id, :email, :display_name, :password_hash, :created_at, :updated_at)
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
            INSERT INTO users (user_id, email, display_name, password_hash)
            VALUES (?, ?, ?, ?)
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
            SELECT user_id, email, display_name, password_hash, created_at, updated_at
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
            SELECT user_id, email, display_name, password_hash, created_at, updated_at
            FROM users
            WHERE user_id = ? AND deleted_at IS NULL
            """,
            (user_id,),
        ).fetchone()
        return _row_to_dict(row)


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
                user_id, processing_run_id, file_name, file_path, public_url,
                media_type, bytes_size
            )
            VALUES (
                :user_id, :processing_run_id, :file_name, :file_path, :public_url,
                :media_type, :bytes_size
            )
            """,
            file_data,
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
