from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from auth import create_access_token, decode_access_token, get_current_user_id
from config import (
    APP_NAME,
    APP_VERSION,
    AUTH_MODE,
    CORS_ALLOW_ORIGINS,
    PUBLIC_BASE_URL,
    ensure_runtime_config,
    get_user_chroma_dir,
    get_user_cv_file,
    get_user_output_dir,
)
from services.main_chat import pipeline
from services.main_curriculo import gerar_pdf_profissional
from services.main_rag import rebuild_vectorstore_for_user
from services.auth_users import authenticate_user, get_user_by_id, register_user
from services.user_data import get_user_profile, save_user_cv, save_user_profile

ensure_runtime_config()

app = FastAPI(title=APP_NAME, version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RequestData(BaseModel):
    texto: str


class UserProfileRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    nome_completo: str | None = None
    email: str | None = None
    telefone: str | None = None
    linkedin: str | None = None
    resumo_profissional: str | None = None
    habilidades: list[str] = Field(default_factory=list)
    idiomas: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    objetivos: list[str] = Field(default_factory=list)
    experiencias: list[str] = Field(default_factory=list)
    formacao: list[str] = Field(default_factory=list)


class AuthRegisterRequest(BaseModel):
    display_name: str
    email: str
    password: str


class AuthLoginRequest(BaseModel):
    email: str
    password: str


def _read_authorization_header(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str | None:
    return authorization


def _require_authorization_header(
    authorization: str | None = Depends(_read_authorization_header),
) -> str:
    if authorization is None:
        raise HTTPException(status_code=401, detail="Header Authorization obrigatorio")

    _, _, token = authorization.partition(" ")
    if not token.strip():
        raise HTTPException(status_code=401, detail="Authorization deve usar Bearer token")

    return token.strip()


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/register")
def auth_register(payload: AuthRegisterRequest) -> dict[str, Any]:
    user = register_user(
        display_name=payload.display_name,
        email=payload.email,
        password=payload.password,
    )
    token = create_access_token(
        user_id=user["user_id"],
        email=user["email"],
        display_name=user["display_name"],
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user,
    }


@app.post("/auth/login")
def auth_login(payload: AuthLoginRequest) -> dict[str, Any]:
    user = authenticate_user(payload.email, payload.password)
    token = create_access_token(
        user_id=user["user_id"],
        email=user["email"],
        display_name=user["display_name"],
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user,
    }


@app.get("/auth/me")
def auth_me(authorization: str = Depends(_require_authorization_header)) -> dict[str, Any]:
    payload = decode_access_token(authorization)
    user_id = payload.get("user_id") or payload.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(status_code=401, detail="Token sem usuario valido")

    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")

    return user


@app.get("/users/me")
def get_current_user(user_id: str = Depends(get_current_user_id)) -> dict[str, str]:
    user = get_user_by_id(user_id)
    response = {
        "user_id": user_id,
        "auth_mode": AUTH_MODE,
    }
    if user:
        response["email"] = user["email"]
        response["display_name"] = user["display_name"]
    return response


@app.post("/users/me/upload-cv")
def upload_cv(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    return save_user_cv(file, user_id)


@app.post("/users/me/profile")
def upsert_profile(
    profile: UserProfileRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    profile_payload = profile.model_dump(exclude_none=True)
    if not profile_payload:
        raise HTTPException(status_code=400, detail="Perfil nao pode ser vazio")

    return save_user_profile(profile_payload, user_id)


@app.get("/users/me/profile")
def read_profile(user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    profile = get_user_profile(user_id)
    if not profile:
        return {
            "user_id": user_id,
            "exists": False,
            "profile": None,
        }

    return {
        "user_id": user_id,
        "exists": True,
        **profile,
    }


@app.get("/users/me/status")
def read_user_status(user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    cv_file = get_user_cv_file(user_id)
    chroma_dir = get_user_chroma_dir(user_id)
    output_dir = get_user_output_dir(user_id)
    profile = get_user_profile(user_id)

    return {
        "user_id": user_id,
        "has_cv": cv_file.exists(),
        "has_profile": profile is not None,
        "has_embeddings": chroma_dir.exists() and any(chroma_dir.iterdir()),
        "generated_files": len([item for item in output_dir.iterdir() if item.is_file()]),
    }


@app.post("/users/me/rebuild-embeddings")
def rebuild_embeddings(user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    return rebuild_vectorstore_for_user(user_id)


@app.get("/users/me/files/{file_name}")
def download_user_file(
    file_name: str,
    user_id: str = Depends(get_current_user_id),
) -> FileResponse:
    safe_file_name = Path(file_name).name
    if safe_file_name != file_name:
        raise HTTPException(status_code=400, detail="Nome de arquivo invalido")

    file_path = get_user_output_dir(user_id) / safe_file_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo nao encontrado")

    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=safe_file_name,
    )


@app.post("/processar")
def processar(
    request_data: RequestData,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    texto_entrada = request_data.texto.strip()
    if not texto_entrada:
        raise HTTPException(status_code=400, detail="Texto nao pode ser vazio")

    try:
        curriculo_otimizado, resposta_usuario = pipeline(texto_entrada, user_id)

        nome_arquivo = f"{uuid.uuid4()}.pdf"
        caminho_pdf = get_user_output_dir(user_id) / nome_arquivo
        gerar_pdf_profissional(curriculo_otimizado, str(caminho_pdf))

        pdf_url = _build_public_file_url(request, nome_arquivo)

        return {
            "texto_resposta": resposta_usuario,
            "pdf_url": pdf_url,
            "user_id": user_id,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _build_public_file_url(request: Request, file_name: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/users/me/files/{file_name}"

    return str(request.url_for("download_user_file", file_name=file_name))
