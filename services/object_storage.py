from __future__ import annotations

from pathlib import Path
from typing import Any

import config
from config import (
    S3_ACCESS_KEY_ID,
    S3_BUCKET,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_ACCESS_KEY,
    S3_SIGNED_URL_EXPIRES,
    S3_SIGNED_URL_MAX_SECONDS,
    sanitize_user_id,
)

_s3_client: Any | None = None


def user_prefix(user_id: str) -> str:
    return f"users/{sanitize_user_id(user_id)}/"


def user_object_key(user_id: str, folder: str, file_name: str) -> str:
    safe_folder = Path(folder).name
    safe_name = Path(file_name).name
    if not safe_folder or not safe_name:
        raise ValueError("object key invalida")
    return f"{user_prefix(user_id)}{safe_folder}/{safe_name}"


def is_remote_storage() -> bool:
    return getattr(config, "OBJECT_STORAGE_BACKEND", "local") == "s3"


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    if is_remote_storage():
        _s3().put_object(
            Bucket=_require_bucket(),
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return key

    path = local_path_for_key(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def put_file(key: str, path: Path, content_type: str = "application/octet-stream") -> str:
    return put_bytes(key, path.read_bytes(), content_type)


def get_bytes(key: str) -> bytes | None:
    if is_remote_storage():
        try:
            response = _s3().get_object(Bucket=_require_bucket(), Key=key)
        except Exception:
            return None
        return response["Body"].read()

    path = local_path_for_key(key)
    if not path.exists() or not path.is_file():
        return None
    return path.read_bytes()


def exists(key: str) -> bool:
    if is_remote_storage():
        try:
            _s3().head_object(Bucket=_require_bucket(), Key=key)
            return True
        except Exception:
            return False
    path = local_path_for_key(key)
    return path.exists() and path.is_file()


def signed_url(key: str, *, expires: int | None = None) -> str | None:
    if not is_remote_storage():
        return None
    ttl = expires if expires is not None else S3_SIGNED_URL_EXPIRES
    ttl = min(max(int(ttl), 1), S3_SIGNED_URL_MAX_SECONDS)
    return _s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": _require_bucket(), "Key": key},
        ExpiresIn=ttl,
    )


def delete_prefix(prefix: str) -> int:
    normalized = prefix if prefix.endswith("/") else f"{prefix}/"
    if is_remote_storage():
        client = _s3()
        bucket = _require_bucket()
        deleted = 0
        continuation: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": normalized}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            listing = client.list_objects_v2(**kwargs)
            objects = [{"Key": item["Key"]} for item in listing.get("Contents") or []]
            if objects:
                client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
                deleted += len(objects)
            if not listing.get("IsTruncated"):
                break
            continuation = listing.get("NextContinuationToken")
        return deleted

    root = local_path_for_key(normalized.rstrip("/"))
    if not root.exists():
        return 0
    removed = 0
    if root.is_file():
        root.unlink()
        return 1
    for path in root.rglob("*"):
        if path.is_file():
            path.unlink()
            removed += 1
    return removed


def purge_user_objects(user_id: str) -> int:
    return delete_prefix(user_prefix(user_id))


def local_path_for_key(key: str) -> Path:
    parts = [part for part in key.split("/") if part and part not in {".", ".."}]
    return config.STORAGE_DIR.joinpath(*parts)


def reset_s3_client() -> None:
    global _s3_client
    _s3_client = None


def set_s3_client(client: Any | None) -> None:
    global _s3_client
    _s3_client = client


def _require_bucket() -> str:
    bucket = getattr(config, "S3_BUCKET", S3_BUCKET)
    if not bucket:
        raise RuntimeError("S3_BUCKET nao configurado para object storage")
    return bucket


def _s3() -> Any:
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    access_key = getattr(config, "S3_ACCESS_KEY_ID", S3_ACCESS_KEY_ID)
    secret_key = getattr(config, "S3_SECRET_ACCESS_KEY", S3_SECRET_ACCESS_KEY)
    if not access_key or not secret_key:
        raise RuntimeError(
            "S3_ACCESS_KEY_ID e S3_SECRET_ACCESS_KEY sao obrigatorios quando "
            "OBJECT_STORAGE_BACKEND=s3"
        )
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Dependencia boto3 nao instalada. Execute pip install -r requirements.txt."
        ) from exc

    kwargs: dict[str, Any] = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": getattr(config, "S3_REGION", S3_REGION),
        "config": BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    }
    endpoint = getattr(config, "S3_ENDPOINT", S3_ENDPOINT)
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    _s3_client = boto3.client("s3", **kwargs)
    return _s3_client
