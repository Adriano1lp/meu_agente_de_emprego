from __future__ import annotations

from pathlib import Path
from typing import Any

from config import (
    OBJECT_STORAGE_BACKEND,
    S3_ACCESS_KEY_ID,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_REGION,
    S3_SECRET_ACCESS_KEY,
    S3_SIGNED_URL_EXPIRES,
    STORAGE_DIR,
    sanitize_user_id,
)

_s3_client: Any | None = None


def user_object_key(user_id: str, folder: str, file_name: str) -> str:
    safe_name = Path(file_name).name
    return f"users/{sanitize_user_id(user_id)}/{folder}/{safe_name}"


def is_remote_storage() -> bool:
    return OBJECT_STORAGE_BACKEND == "s3"


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    if is_remote_storage():
        _s3().put_object(
            Bucket=_require_bucket(),
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return key

    path = _local_path(key)
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

    path = _local_path(key)
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
    path = _local_path(key)
    return path.exists() and path.is_file()


def signed_url(key: str, *, expires: int | None = None) -> str | None:
    if not is_remote_storage():
        return None
    return _s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": _require_bucket(), "Key": key},
        ExpiresIn=expires or S3_SIGNED_URL_EXPIRES,
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

    root = _local_path(normalized.rstrip("/"))
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


def local_path_for_key(key: str) -> Path:
    return _local_path(key)


def _local_path(key: str) -> Path:
    parts = [part for part in key.split("/") if part and part not in {".", ".."}]
    return STORAGE_DIR.joinpath(*parts)


def _require_bucket() -> str:
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET nao configurado para object storage")
    return S3_BUCKET


def _s3() -> Any:
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    if not S3_ACCESS_KEY_ID or not S3_SECRET_ACCESS_KEY:
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
        "aws_access_key_id": S3_ACCESS_KEY_ID,
        "aws_secret_access_key": S3_SECRET_ACCESS_KEY,
        "region_name": S3_REGION,
        "config": BotoConfig(signature_version="s3v4"),
    }
    if S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = S3_ENDPOINT_URL
    _s3_client = boto3.client("s3", **kwargs)
    return _s3_client
