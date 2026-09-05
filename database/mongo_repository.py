from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from config import MONGODB_DATABASE, MONGODB_URI

_client: Any | None = None
_database: Any | None = None
_indexes_ready = False


def is_configured() -> bool:
    return bool(MONGODB_URI)


def initialize_database() -> Any:
    database = _get_database()
    _ensure_indexes(database)
    return database


def create_user(user: dict[str, Any], *, consents: list[dict[str, Any]] | None = None) -> None:
    _get_collection("users").insert_one({**user, "deleted_at": None})
    for consent in consents or []:
        append_consent_log(consent)


def ensure_user_exists(user_id: str) -> None:
    users = _get_collection("users")
    if users.find_one({"user_id": user_id, "deleted_at": None}, {"_id": 1}):
        return

    now = _utc_now_iso()
    users.update_one(
        {"user_id": user_id},
        {
            "$setOnInsert": {
                "user_id": user_id,
                "email": f"{user_id}@local.invalid",
                "display_name": user_id,
                "password_hash": "legacy_external_auth",
                "terms_accepted": False,
                "terms_accepted_at": None,
                "terms_version": None,
                "privacy_accepted": False,
                "privacy_accepted_at": None,
                "privacy_version": None,
                "stripe_customer_id": None,
                "stripe_subscription_id": None,
                "plan": "free",
                "subscription_status": "none",
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
            },
        },
        upsert=True,
    )


def get_user_by_email(email: str) -> dict[str, Any] | None:
    user = _get_collection("users").find_one(
        {"email": email, "deleted_at": None},
        {"_id": 0},
    )
    return dict(user) if user else None


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    user = _get_collection("users").find_one(
        {"user_id": user_id, "deleted_at": None},
        {"_id": 0},
    )
    return dict(user) if user else None


def is_deleted_user(user_id: str) -> bool:
    user = _get_collection("users").find_one(
        {"user_id": user_id, "deleted_at": {"$ne": None}},
        {"_id": 1},
    )
    return user is not None


def user_id_exists(user_id: str) -> bool:
    user = _get_collection("users").find_one(
        {"user_id": user_id},
        {"_id": 1},
    )
    return user is not None


def accept_user_terms(user_id: str, accepted_at: str, *, version: str | None = None) -> dict[str, Any] | None:
    return update_user_consent(
        user_id,
        doc="terms",
        version=version or "",
        accepted_at=accepted_at,
    )


def update_user_consent(
    user_id: str,
    *,
    doc: str,
    version: str,
    accepted_at: str,
) -> dict[str, Any] | None:
    if doc == "terms":
        fields = {
            "terms_accepted": True,
            "terms_accepted_at": accepted_at,
            "terms_version": version,
            "updated_at": accepted_at,
        }
    elif doc == "privacy":
        fields = {
            "privacy_accepted": True,
            "privacy_accepted_at": accepted_at,
            "privacy_version": version,
            "updated_at": accepted_at,
        }
    else:
        raise ValueError("doc deve ser terms ou privacy")

    users = _get_collection("users")
    users.update_one(
        {"user_id": user_id, "deleted_at": None},
        {"$set": fields},
    )
    user = users.find_one(
        {"user_id": user_id, "deleted_at": None},
        {"_id": 0},
    )
    return dict(user) if user else None


def append_consent_log(consent: dict[str, Any]) -> None:
    _get_collection("consent_log").insert_one(dict(consent))


def list_consent_log(user_id: str) -> list[dict[str, Any]]:
    documents = _get_collection("consent_log").find(
        {"user_id": user_id},
        {"_id": 0, "user_id": 1, "doc": 1, "version": 1, "accepted_at": 1},
        sort=[("accepted_at", 1), ("_id", 1)],
    )
    return [dict(document) for document in documents]


def update_user_password_hash(
    *,
    user_id: str,
    password_hash: str,
    updated_at: str,
) -> dict[str, Any] | None:
    users = _get_collection("users")
    users.update_one(
        {"user_id": user_id, "deleted_at": None},
        {"$set": {"password_hash": password_hash, "updated_at": updated_at}},
    )
    user = users.find_one(
        {"user_id": user_id, "deleted_at": None},
        {"_id": 0},
    )
    return dict(user) if user else None


def create_password_reset_token(token_data: dict[str, Any]) -> None:
    _get_collection("password_reset_tokens").insert_one(token_data)


def get_password_reset_token_by_hash(token_hash: str) -> dict[str, Any] | None:
    token = _get_collection("password_reset_tokens").find_one(
        {"token_hash": token_hash},
        {"_id": 0},
    )
    return dict(token) if token else None


def mark_password_reset_token_used(*, token_hash: str, used_at: str) -> None:
    _get_collection("password_reset_tokens").update_one(
        {"token_hash": token_hash, "used_at": None},
        {"$set": {"used_at": used_at}},
    )

def upsert_user_profile(user_id: str, version: int, profile_data: dict[str, Any]) -> None:
    ensure_user_exists(user_id)
    now = _utc_now_iso()
    profile = {
        "user_id": user_id,
        "version": version,
        "nome_completo": profile_data.get("nome_completo"),
        "email": profile_data.get("email"),
        "telefone": profile_data.get("telefone"),
        "linkedin": profile_data.get("linkedin"),
        "resumo_profissional": profile_data.get("resumo_profissional"),
        "profile": profile_data,
        "updated_at": now,
    }
    _get_collection("user_profiles").update_one(
        {"user_id": user_id},
        {
            "$set": profile,
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    _get_collection("user_profile_versions").update_one(
        {"user_id": user_id, "version": version},
        {
            "$setOnInsert": {
                "user_id": user_id,
                "version": version,
                "profile": profile_data,
                "created_at": now,
            },
        },
        upsert=True,
    )


def get_user_profile(user_id: str) -> dict[str, Any] | None:
    profile = _get_collection("user_profiles").find_one(
        {"user_id": user_id},
        {"_id": 0, "user_id": 1, "version": 1, "profile": 1, "updated_at": 1},
    )
    return dict(profile) if profile else None


def create_user_document(document: dict[str, Any]) -> str:
    ensure_user_exists(document["user_id"])
    now = _utc_now_iso()
    payload = {
        **document,
        "created_at": now,
        "updated_at": now,
    }
    result = _get_collection("user_documents").insert_one(payload)
    return str(result.inserted_id)


def get_latest_user_document_id(user_id: str) -> str | None:
    document = _get_collection("user_documents").find_one(
        {"user_id": user_id, "document_type": "cv"},
        sort=[("created_at", -1), ("_id", -1)],
        projection={"_id": 1},
    )
    return str(document["_id"]) if document else None


def get_latest_user_cv_text(user_id: str) -> str | None:
    document = _get_collection("user_documents").find_one(
        {"user_id": user_id, "document_type": "cv"},
        sort=[("created_at", -1), ("_id", -1)],
        projection={"_id": 0, "extracted_text": 1},
    )
    if not document:
        return None
    text = str(document.get("extracted_text") or "").strip()
    return text or None


def create_embedding_run(run: dict[str, Any]) -> str:
    ensure_user_exists(run["user_id"])
    payload = {**run, "created_at": _utc_now_iso()}
    result = _get_collection("embedding_runs").insert_one(payload)
    return str(result.inserted_id)


def replace_embedding_chunks(
    *,
    user_id: str,
    embedding_run_id: str,
    embedding_model: str,
    chunks: list[dict[str, Any]],
) -> None:
    collection = _get_collection("embedding_chunks")
    collection.delete_many({"user_id": user_id})
    if not chunks:
        return

    now = _utc_now_iso()
    collection.insert_many(
        [
            {
                "user_id": user_id,
                "embedding_run_id": embedding_run_id,
                "embedding_model": embedding_model,
                "chunk_index": index,
                "content": chunk["content"],
                "metadata": chunk.get("metadata", {}),
                "embedding": chunk["embedding"],
                "created_at": now,
            }
            for index, chunk in enumerate(chunks)
        ],
    )


def count_embedding_chunks(user_id: str) -> int:
    return int(_get_collection("embedding_chunks").count_documents({"user_id": user_id}))


def find_similar_embedding_chunks(
    *,
    user_id: str,
    query_embedding: list[float],
    limit: int = 6,
) -> list[dict[str, Any]]:
    documents = _get_collection("embedding_chunks").find(
        {"user_id": user_id},
        {"_id": 0, "content": 1, "metadata": 1, "embedding": 1},
    )
    scored: list[tuple[float, dict[str, Any]]] = []
    for document in documents:
        score = _cosine_similarity(query_embedding, document.get("embedding") or [])
        scored.append((score, document))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "page_content": document.get("content", ""),
            "metadata": document.get("metadata") or {},
            "score": score,
        }
        for score, document in scored[:limit]
    ]


def create_processing_run(run: dict[str, Any]) -> str:
    ensure_user_exists(run["user_id"])
    payload = {
        **run,
        "created_at": _utc_now_iso(),
    }
    result = _get_collection("processing_runs").insert_one(payload)
    return str(result.inserted_id)


def create_job_analysis_insight(insight: dict[str, Any]) -> str:
    ensure_user_exists(insight["user_id"])
    now = _utc_now_iso()
    payload = {
        **insight,
        "created_at": insight.get("created_at") or now,
        "updated_at": now,
    }
    result = _get_collection("job_analysis_insights").update_one(
        {
            "user_id": insight["user_id"],
            "processing_run_id": insight.get("processing_run_id"),
        },
        {
            "$set": payload,
            "$setOnInsert": {"inserted_at": now},
        },
        upsert=True,
    )
    return str(result.upserted_id or insight.get("processing_run_id") or "")


def list_job_analysis_insights(
    user_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    documents = _get_collection("job_analysis_insights").find(
        {"user_id": user_id},
        {"_id": 1, "user_id": 0, "updated_at": 0, "inserted_at": 0},
        sort=[("created_at", -1), ("_id", -1)],
        skip=offset,
        limit=limit,
    )
    return [_mongo_insight_to_dict(document) for document in documents]


def create_generated_file(file_data: dict[str, Any]) -> str:
    ensure_user_exists(file_data["user_id"])
    result = _get_collection("generated_files").insert_one(
        {**file_data, "created_at": _utc_now_iso()},
    )
    return str(result.inserted_id)


def create_development_plan(plan: dict[str, Any]) -> str:
    ensure_user_exists(plan["user_id"])
    payload = {
        **plan,
        "created_at": plan.get("created_at") or _utc_now_iso(),
        "updated_at": plan.get("updated_at") or _utc_now_iso(),
    }
    _get_collection("development_plans").insert_one(payload)
    return str(plan["pdi_id"])


def update_development_plan(plan: dict[str, Any]) -> None:
    _get_collection("development_plans").update_one(
        {
            "user_id": plan["user_id"],
            "pdi_id": plan["pdi_id"],
        },
        {
            "$set": {
                key: value
                for key, value in plan.items()
                if key not in {"_id", "pdi_id", "user_id", "created_at"}
            },
        },
    )


def get_active_development_plan(user_id: str) -> dict[str, Any] | None:
    plan = _get_collection("development_plans").find_one(
        {"user_id": user_id, "status": "active"},
        {"_id": 0},
        sort=[("created_at", -1), ("pdi_id", -1)],
    )
    return dict(plan) if plan else None


def get_development_plan(user_id: str, pdi_id: str) -> dict[str, Any] | None:
    plan = _get_collection("development_plans").find_one(
        {"user_id": user_id, "pdi_id": pdi_id},
        {"_id": 0},
    )
    return dict(plan) if plan else None


def list_development_plans(
    user_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    documents = _get_collection("development_plans").find(
        {"user_id": user_id},
        {"_id": 0},
        sort=[("created_at", -1), ("pdi_id", -1)],
        skip=offset,
        limit=limit,
    )
    return [dict(document) for document in documents]


def count_generated_files(user_id: str) -> int:
    return int(_get_collection("generated_files").count_documents({"user_id": user_id}))


def update_user_billing(
    user_id: str,
    *,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    plan: str | None = None,
    subscription_status: str | None = None,
    updated_at: str,
    clear_subscription_id: bool = False,
) -> dict[str, Any] | None:
    fields: dict[str, Any] = {"updated_at": updated_at}
    if stripe_customer_id is not None:
        fields["stripe_customer_id"] = stripe_customer_id
    if clear_subscription_id:
        fields["stripe_subscription_id"] = None
    elif stripe_subscription_id is not None:
        fields["stripe_subscription_id"] = stripe_subscription_id
    if plan is not None:
        fields["plan"] = plan
    if subscription_status is not None:
        fields["subscription_status"] = subscription_status

    users = _get_collection("users")
    users.update_one(
        {"user_id": user_id, "deleted_at": None},
        {"$set": fields},
    )
    user = users.find_one(
        {"user_id": user_id, "deleted_at": None},
        {"_id": 0},
    )
    return dict(user) if user else None


def get_user_by_stripe_customer_id(customer_id: str) -> dict[str, Any] | None:
    user = _get_collection("users").find_one(
        {"stripe_customer_id": customer_id, "deleted_at": None},
        {"_id": 0},
    )
    return dict(user) if user else None


def get_user_by_stripe_subscription_id(subscription_id: str) -> dict[str, Any] | None:
    user = _get_collection("users").find_one(
        {"stripe_subscription_id": subscription_id, "deleted_at": None},
        {"_id": 0},
    )
    return dict(user) if user else None


def get_processar_usage(user_id: str, period: str) -> int:
    document = _get_collection("processar_usage").find_one(
        {"user_id": user_id, "period": period},
        {"_id": 0, "used": 1},
    )
    return int(document["used"]) if document else 0


def consume_processar_usage(user_id: str, *, period: str, limit: int) -> dict[str, Any]:
    try:
        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Dependencia pymongo nao instalada. Execute pip install -r requirements.txt."
        ) from exc

    ensure_user_exists(user_id)
    collection = _get_collection("processar_usage")
    now = _utc_now_iso()
    document = collection.find_one_and_update(
        {"user_id": user_id, "period": period, "used": {"$lt": limit}},
        {"$inc": {"used": 1}, "$set": {"updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if document:
        return {"allowed": True, "used": int(document["used"])}

    existing = collection.find_one({"user_id": user_id, "period": period})
    if existing:
        return {"allowed": False, "used": int(existing.get("used") or 0)}

    try:
        collection.insert_one(
            {
                "user_id": user_id,
                "period": period,
                "used": 1,
                "updated_at": now,
            }
        )
        return {"allowed": True, "used": 1}
    except DuplicateKeyError:
        return consume_processar_usage(user_id, period=period, limit=limit)


def claim_stripe_webhook_event(event_id: str, event_type: str) -> bool:
    try:
        from pymongo.errors import DuplicateKeyError
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Dependencia pymongo nao instalada. Execute pip install -r requirements.txt."
        ) from exc

    try:
        _get_collection("stripe_webhook_events").insert_one(
            {
                "event_id": event_id,
                "event_type": event_type,
                "processed_at": _utc_now_iso(),
            }
        )
        return True
    except DuplicateKeyError:
        return False


def collect_user_export_payload(user_id: str) -> dict[str, Any]:
    user = get_user_by_id(user_id)
    if not user:
        return {}

    user.pop("password_hash", None)
    profile = get_user_profile(user_id)
    documents = [
        {
            "original_filename": document.get("original_filename"),
            "original_content_type": document.get("original_content_type"),
            "document_type": document.get("document_type"),
            "object_key": document.get("object_key"),
            "created_at": document.get("created_at"),
        }
        for document in _get_collection("user_documents").find(
            {"user_id": user_id},
            {
                "_id": 0,
                "original_filename": 1,
                "original_content_type": 1,
                "document_type": 1,
                "object_key": 1,
                "created_at": 1,
            },
            sort=[("created_at", 1)],
        )
    ]
    processing_runs = [
        _public_mongo_document(document)
        for document in _get_collection("processing_runs").find(
            {"user_id": user_id},
            sort=[("created_at", 1)],
        )
    ]
    generated_files = [
        {
            "file_name": document.get("file_name"),
            "media_type": document.get("media_type"),
            "object_key": document.get("object_key"),
            "created_at": document.get("created_at"),
        }
        for document in _get_collection("generated_files").find(
            {"user_id": user_id},
            {"_id": 0, "file_name": 1, "media_type": 1, "object_key": 1, "created_at": 1},
            sort=[("created_at", 1)],
        )
    ]
    usage = [
        {
            "period": document.get("period"),
            "used": int(document.get("used") or 0),
            "updated_at": document.get("updated_at"),
        }
        for document in _get_collection("processar_usage").find(
            {"user_id": user_id},
            {"_id": 0, "period": 1, "used": 1, "updated_at": 1},
            sort=[("period", 1)],
        )
    ]
    return {
        "user": user,
        "profile": profile,
        "processing_runs": processing_runs,
        "job_analysis_insights": list_job_analysis_insights(
            user_id,
            limit=1000,
            offset=0,
        ),
        "development_plans": list_development_plans(user_id, limit=1000, offset=0),
        "documents": documents,
        "generated_files": generated_files,
        "processar_usage": usage,
    }


def anonymize_and_purge_user(user_id: str, *, deleted_at: str) -> bool:
    users = _get_collection("users")
    if not users.find_one({"user_id": user_id, "deleted_at": None}, {"_id": 1}):
        return False

    for collection_name in (
        "generated_files",
        "job_analysis_insights",
        "development_plans",
        "processing_runs",
        "embedding_runs",
        "embedding_chunks",
        "user_documents",
        "user_profile_versions",
        "user_profiles",
        "password_reset_tokens",
        "processar_usage",
    ):
        _get_collection(collection_name).delete_many({"user_id": user_id})

    users.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "email": f"deleted+{user_id}@invalid.local",
                "display_name": "Conta excluida",
                "password_hash": "deleted_account",
                "stripe_customer_id": None,
                "stripe_subscription_id": None,
                "plan": "free",
                "subscription_status": "none",
                "updated_at": deleted_at,
                "deleted_at": deleted_at,
            },
        },
    )
    return True


def _public_mongo_document(document: dict[str, Any]) -> dict[str, Any]:
    payload = dict(document)
    if "_id" in payload:
        payload["id"] = str(payload.pop("_id"))
    payload.pop("password_hash", None)
    return payload


def _get_collection(name: str) -> Any:
    database = initialize_database()
    return database[name]


def _get_database() -> Any:
    global _client, _database

    if not MONGODB_URI:
        raise RuntimeError("MONGODB_URI nao configurada")

    if _database is None:
        try:
            from pymongo import MongoClient
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Dependencia pymongo nao instalada. Execute pip install -r requirements.txt."
            ) from exc

        _client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        _database = _client[MONGODB_DATABASE]

    return _database


def _ensure_indexes(database: Any) -> None:
    global _indexes_ready
    if _indexes_ready:
        return

    database.users.create_index("email", unique=True)
    database.users.create_index("user_id", unique=True)
    database.users.create_index("stripe_customer_id")
    database.users.create_index("stripe_subscription_id")
    database.consent_log.create_index([("user_id", 1), ("accepted_at", 1)])
    database.password_reset_tokens.create_index("token_hash", unique=True)
    database.password_reset_tokens.create_index([("user_id", 1), ("created_at", -1)])
    database.password_reset_tokens.create_index("expires_at")
    database.user_profiles.create_index("user_id", unique=True)
    database.user_profile_versions.create_index(
        [("user_id", 1), ("version", 1)],
        unique=True,
    )
    database.user_documents.create_index([("user_id", 1), ("created_at", -1)])
    database.embedding_runs.create_index([("user_id", 1), ("processed_at", -1)])
    database.embedding_chunks.create_index("user_id")
    database.processing_runs.create_index([("user_id", 1), ("created_at", -1)])
    database.job_analysis_insights.create_index([("user_id", 1), ("created_at", -1)])
    database.job_analysis_insights.create_index(
        [("user_id", 1), ("processing_run_id", 1)],
        unique=True,
    )
    database.development_plans.create_index([("user_id", 1), ("created_at", -1)])
    database.development_plans.create_index([("user_id", 1), ("status", 1)])
    database.development_plans.create_index(
        [("user_id", 1), ("pdi_id", 1)],
        unique=True,
    )
    database.generated_files.create_index([("user_id", 1), ("created_at", -1)])
    database.processar_usage.create_index(
        [("user_id", 1), ("period", 1)],
        unique=True,
    )
    database.stripe_webhook_events.create_index("event_id", unique=True)
    _indexes_ready = True


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0

    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0

    return dot / (left_norm * right_norm)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _mongo_insight_to_dict(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(document.get("_id")),
        "processing_run_id": document.get("processing_run_id"),
        "created_at": document.get("created_at"),
        "job_title": document.get("job_title"),
        "company_name": document.get("company_name"),
        "job_summary": document.get("job_summary"),
        "match_score": int(document.get("match_score") or 0),
        "strengths": _list_or_empty(document.get("strengths")),
        "critical_gaps": _list_or_empty(document.get("critical_gaps")),
        "matching_skills": _list_or_empty(document.get("matching_skills")),
        "missing_skills": _list_or_empty(document.get("missing_skills")),
        "status": document.get("status"),
        "generation_blocked": bool(document.get("generation_blocked")),
        "blocked_reason": document.get("blocked_reason"),
        "source": document.get("source"),
    }


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
