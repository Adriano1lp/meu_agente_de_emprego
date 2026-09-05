from __future__ import annotations

from config import get_user_output_dir
from services.object_storage import (
    delete_prefix,
    exists,
    get_bytes,
    is_remote_storage,
    put_bytes,
    signed_url,
    user_object_key,
)


def _register(client, email: str) -> dict[str, str]:
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Usuario Storage",
            "email": email,
            "password": "senha-forte-123",
            "terms_accepted": True,
            "privacy_accepted": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    return {
        "user_id": body["user"]["user_id"],
        "auth": {"Authorization": f"Bearer {body['access_token']}"},
    }


def test_local_backend_put_get_and_delete_prefix() -> None:
    assert is_remote_storage() is False
    key = "users/user_storage_test/outputs/sample.pdf"
    put_bytes(key, b"%PDF-1.4 local", "application/pdf")
    assert exists(key) is True
    assert get_bytes(key) == b"%PDF-1.4 local"
    assert signed_url(key) is None
    assert delete_prefix("users/user_storage_test") >= 1
    assert exists(key) is False


def test_download_generated_pdf_from_local_storage(client) -> None:
    session = _register(client, "storage.pdf@example.com")
    file_name = "curriculo-teste.pdf"
    output_path = get_user_output_dir(session["user_id"]) / file_name
    output_path.write_bytes(b"%PDF-1.4 curriculo")
    put_bytes(
        user_object_key(session["user_id"], "outputs", file_name),
        output_path.read_bytes(),
        "application/pdf",
    )

    response = client.get(f"/users/me/files/{file_name}", headers=session["auth"])
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-1.4")
