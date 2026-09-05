from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config import (
    CURRENT_PRIVACY_VERSION,
    CURRENT_TERMS_VERSION,
    S3_SIGNED_URL_MAX_SECONDS,
    ensure_runtime_config,
    s3_configured,
)
from services.object_storage import (
    delete_prefix,
    exists,
    get_bytes,
    is_remote_storage,
    purge_user_objects,
    put_bytes,
    reset_s3_client,
    set_s3_client,
    signed_url,
    user_object_key,
    user_prefix,
)


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    def __init__(self, bucket: str = "test-bucket") -> None:
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}
        self.last_signed_expires: int | None = None

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        assert Bucket == self.bucket
        self.objects[Key] = Body if isinstance(Body, bytes) else bytes(Body)

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, _FakeBody]:
        if Key not in self.objects:
            raise RuntimeError("NoSuchKey")
        return {"Body": _FakeBody(self.objects[Key])}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, int]:
        if Key not in self.objects:
            raise RuntimeError("NoSuchKey")
        return {"ContentLength": len(self.objects[Key])}

    def list_objects_v2(self, *, Bucket: str, Prefix: str, ContinuationToken: str | None = None):
        keys = [key for key in self.objects if key.startswith(Prefix)]
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}

    def delete_objects(self, *, Bucket: str, Delete: dict[str, list[dict[str, str]]]) -> None:
        for item in Delete.get("Objects") or []:
            self.objects.pop(item["Key"], None)

    def generate_presigned_url(self, _operation: str, Params: dict, ExpiresIn: int) -> str:
        self.last_signed_expires = ExpiresIn
        return f"https://r2.example/{Params['Key']}?expires={ExpiresIn}"


def _register(client: TestClient, email: str) -> dict[str, object]:
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Usuario Storage",
            "email": email,
            "password": "senha-forte-123",
            "terms_accepted": True,
            "terms_version": CURRENT_TERMS_VERSION,
            "privacy_accepted": True,
            "privacy_version": CURRENT_PRIVACY_VERSION,
        },
    )
    assert response.status_code == 200
    body = response.json()
    return {
        "user_id": body["user"]["user_id"],
        "auth": {"Authorization": f"Bearer {body['access_token']}"},
    }


def test_local_backend_put_get_and_delete_prefix(isolated_db) -> None:
    assert is_remote_storage() is False
    key = "users/user_storage_test/outputs/sample.pdf"
    put_bytes(key, b"%PDF-1.4 local", "application/pdf")
    assert exists(key) is True
    assert get_bytes(key) == b"%PDF-1.4 local"
    assert signed_url(key) is None
    assert delete_prefix("users/user_storage_test") >= 1
    assert exists(key) is False


def test_s3_stub_put_signed_url_and_purge(isolated_db, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeS3Client()
    monkeypatch.setattr("config.OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr("config.S3_BUCKET", "test-bucket")
    set_s3_client(fake)
    try:
        key = user_object_key("user_r2", "outputs", "curriculo.pdf")
        assert key == "users/user_r2/outputs/curriculo.pdf"
        put_bytes(key, b"%PDF-1.4 r2", "application/pdf")
        assert fake.objects[key] == b"%PDF-1.4 r2"
        assert exists(key) is True
        assert get_bytes(key) == b"%PDF-1.4 r2"

        url = signed_url(key, expires=3600)
        assert url is not None
        assert fake.last_signed_expires == S3_SIGNED_URL_MAX_SECONDS
        assert fake.last_signed_expires <= 15 * 60

        assert purge_user_objects("user_r2") == 1
        assert exists(key) is False
        assert fake.objects == {}
    finally:
        reset_s3_client()
        monkeypatch.setattr("config.OBJECT_STORAGE_BACKEND", "local")


def test_download_generated_pdf_owner_only(isolated_db) -> None:
    from main import app

    client = TestClient(app)
    owner = _register(client, "owner.storage@example.com")
    other = _register(client, "other.storage@example.com")
    file_name = "curriculo-teste.pdf"
    put_bytes(
        user_object_key(str(owner["user_id"]), "outputs", file_name),
        b"%PDF-1.4 curriculo",
        "application/pdf",
    )

    response = client.get(f"/users/me/files/{file_name}", headers=owner["auth"])
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-1.4")

    denied = client.get(f"/users/me/files/{file_name}", headers=other["auth"])
    assert denied.status_code == 404

    anonymous = client.get(f"/users/me/files/{file_name}")
    assert anonymous.status_code == 401


def test_download_uses_s3_stub_when_disk_is_empty(
    isolated_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeS3Client()
    monkeypatch.setattr("config.OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr("config.S3_BUCKET", "test-bucket")
    set_s3_client(fake)
    try:
        from main import app

        client = TestClient(app)
        owner = _register(client, "remote.pdf@example.com")
        file_name = "remoto.pdf"
        key = user_object_key(str(owner["user_id"]), "outputs", file_name)
        put_bytes(key, b"%PDF-1.4 remoto", "application/pdf")

        response = client.get(f"/users/me/files/{file_name}", headers=owner["auth"])
        assert response.status_code == 200
        assert response.content == b"%PDF-1.4 remoto"
    finally:
        reset_s3_client()
        monkeypatch.setattr("config.OBJECT_STORAGE_BACKEND", "local")


def test_delete_account_purges_object_prefix(isolated_db, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeS3Client()
    monkeypatch.setattr("config.OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setattr("config.S3_BUCKET", "test-bucket")
    set_s3_client(fake)
    try:
        from main import app

        client = TestClient(app)
        session = _register(client, "purge.r2@example.com")
        user_id = str(session["user_id"])
        cv_key = user_object_key(user_id, "documents", "cv_original.pdf")
        pdf_key = user_object_key(user_id, "outputs", "gerado.pdf")
        put_bytes(cv_key, b"%PDF-1.4 cv", "application/pdf")
        put_bytes(pdf_key, b"%PDF-1.4 out", "application/pdf")
        assert user_prefix(user_id) == f"users/{user_id}/"
        assert fake.objects[cv_key]

        response = client.request(
            "DELETE",
            "/users/me",
            headers=session["auth"],
            json={"confirm": "DELETE"},
        )
        assert response.status_code == 200
        assert exists(cv_key) is False
        assert exists(pdf_key) is False
        assert all(not key.startswith(user_prefix(user_id)) for key in fake.objects)
    finally:
        reset_s3_client()
        monkeypatch.setattr("config.OBJECT_STORAGE_BACKEND", "local")


def test_production_boot_fails_without_s3_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.ENVIRONMENT", "production")
    monkeypatch.setattr("config.AUTH_MODE", "jwt")
    monkeypatch.setattr("config.JWT_SECRET", "segredo-forte-de-producao")
    monkeypatch.setattr("config.CORS_ALLOW_ORIGINS", ["https://app.example"])
    monkeypatch.setattr("config.PERSISTENCE_BACKEND", "mongodb")
    monkeypatch.setattr("config.MONGODB_URI", "mongodb://localhost")
    monkeypatch.setattr("config.S3_ENDPOINT", "")
    monkeypatch.setattr("config.S3_BUCKET", "")
    monkeypatch.setattr("config.S3_ACCESS_KEY_ID", "")
    monkeypatch.setattr("config.S3_SECRET_ACCESS_KEY", "")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ENVIRONMENT", "production")

    assert s3_configured() is False
    with pytest.raises(RuntimeError, match="Object storage S3/R2 obrigatorio"):
        ensure_runtime_config()


def test_dev_boot_allows_missing_s3_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.ENVIRONMENT", "development")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr("config.S3_ENDPOINT", "")
    monkeypatch.setattr("config.S3_BUCKET", "")
    monkeypatch.setattr("config.S3_ACCESS_KEY_ID", "")
    monkeypatch.setattr("config.S3_SECRET_ACCESS_KEY", "")
    ensure_runtime_config()
