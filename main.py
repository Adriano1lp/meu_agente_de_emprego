from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from auth import create_access_token, decode_access_token, get_current_user_id
from config import (
    APP_NAME,
    APP_VERSION,
    AUTH_MODE,
    CORS_ALLOW_ORIGINS,
    PERSISTENCE_BACKEND,
    PUBLIC_BASE_URL,
    ensure_runtime_config,
    get_user_chroma_dir,
    get_user_cv_file,
    get_user_output_dir,
)
from database.repository import (
    count_embedding_chunks,
    count_generated_files,
    create_generated_file,
    create_job_analysis_insight,
    create_processing_run,
    get_latest_user_document_id,
    list_job_analysis_insights,
)
from services.main_chat import generate_cover_letter, pipeline_with_details
from services.main_carta import gerar_pdf_carta_apresentacao
from services.main_curriculo import gerar_pdf_profissional
from services.main_rag import rebuild_vectorstore_for_user
from services.auth_users import (
    accept_terms_for_user,
    authenticate_user,
    confirm_password_reset,
    get_user_by_id,
    register_user,
    request_password_reset,
    user_can_access_terms_protected_routes,
)
from services.account import delete_current_user, export_current_user
from services.billing import (
    consume_llm_quota,
    create_checkout_session,
    get_entitlement,
    handle_stripe_webhook,
)
from services.legal import current_legal_documents
from services.development_plan import (
    DEFAULT_ANALYSIS_LIMIT,
    MAX_ANALYSIS_LIMIT,
    generate_development_plan,
    read_active_development_plan,
    read_development_plan_history,
    update_development_plan_item_status,
)
from services.user_data import (
    get_user_profile,
    save_manual_profile,
    save_user_cv,
    save_user_profile,
)

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


class CoverLetterRequest(BaseModel):
    empresa: str


class DevelopmentPlanGenerateRequest(BaseModel):
    limit: int = Field(default=DEFAULT_ANALYSIS_LIMIT, ge=1, le=MAX_ANALYSIS_LIMIT)
    replace_active: bool = False


class DevelopmentPlanItemStatusRequest(BaseModel):
    status: str


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


class ManualEducationRequest(BaseModel):
    instituicao: str
    curso: str
    grau: str | None = None
    ano_inicio: str | None = None
    ano_conclusao: str | None = None
    status: str | None = None
    detalhes: str | None = None


class ManualExperienceRequest(BaseModel):
    empresa: str
    cargo: str
    area: str | None = None
    data_inicio: str | None = None
    data_fim: str | None = None
    emprego_atual: bool = False
    atividades: str
    responsabilidades: str | None = None
    resultados: str | None = None
    ferramentas: str | None = None
    palavras_chave: str | None = None


class ManualProfileRequest(BaseModel):
    titulo_profissional: str | None = None
    resumo_profissional: str | None = None
    objetivos_profissionais: str | None = None
    senioridade: str | None = None
    modelo_trabalho: str | None = None
    disponibilidade: str | None = None
    formacoes: list[ManualEducationRequest] = Field(default_factory=list)
    experiencias: list[ManualExperienceRequest] = Field(default_factory=list)
    habilidades_tecnicas: list[str] = Field(default_factory=list)
    ferramentas: list[str] = Field(default_factory=list)
    idiomas: list[str] = Field(default_factory=list)
    certificacoes: list[str] = Field(default_factory=list)
    projetos: list[str] = Field(default_factory=list)
    atividades_complementares: list[str] = Field(default_factory=list)


def _has_profile_content(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)

    return value is not None


def _build_profile_payload(profile: UserProfileRequest) -> dict[str, Any]:
    payload = profile.model_dump(exclude_none=True, exclude_unset=True)
    return {
        field: value
        for field, value in payload.items()
        if _has_profile_content(value)
    }


class AuthRegisterRequest(BaseModel):
    display_name: str
    email: str
    password: str
    terms_accepted: bool = False
    privacy_accepted: bool = False


class AuthLoginRequest(BaseModel):
    email: str
    password: str


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str


class TermsAcceptanceRequest(BaseModel):
    accepted: bool
    privacy_accepted: bool | None = None


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


def _require_terms_accepted(user_id: str = Depends(get_current_user_id)) -> str:
    can_access = user_can_access_terms_protected_routes(user_id)
    if can_access is None:
        return user_id
    if not can_access:
        raise HTTPException(
            status_code=403,
            detail="Aceite do termo de uso obrigatorio",
        )
    return user_id


def require_llm_quota(feature: str):
    def _dependency(user_id: str = Depends(_require_terms_accepted)) -> str:
        consume_llm_quota(user_id, feature)
        return user_id

    return _dependency


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/legal")
def read_current_legal_documents() -> dict[str, Any]:
    return current_legal_documents()


@app.post("/auth/register")
def auth_register(payload: AuthRegisterRequest, request: Request) -> dict[str, Any]:
    user = register_user(
        display_name=payload.display_name,
        email=payload.email,
        password=payload.password,
        terms_accepted=payload.terms_accepted,
        privacy_accepted=payload.privacy_accepted,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
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


@app.post("/auth/password-reset/request")
def auth_password_reset_request(payload: PasswordResetRequest) -> dict[str, Any]:
    return request_password_reset(payload.email)


@app.post("/auth/password-reset/confirm")
def auth_password_reset_confirm(payload: PasswordResetConfirmRequest) -> dict[str, str]:
    return confirm_password_reset(payload.token, payload.new_password)


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
def get_current_user(user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    user = get_user_by_id(user_id)
    response = {
        "user_id": user_id,
        "auth_mode": AUTH_MODE,
        "terms_accepted": bool(user.get("terms_accepted")) if user else False,
        "terms_accepted_at": user.get("terms_accepted_at") if user else None,
        "terms_version": user.get("terms_version") if user else None,
        "privacy_accepted": bool(user.get("privacy_accepted")) if user else False,
        "privacy_accepted_at": user.get("privacy_accepted_at") if user else None,
        "privacy_version": user.get("privacy_version") if user else None,
        "needs_reconsent": bool(user.get("needs_reconsent")) if user else True,
    }
    if user:
        response["email"] = user["email"]
        response["display_name"] = user["display_name"]
        response["billing"] = get_entitlement(user_id)
    return response


@app.post("/users/me/terms/accept")
def accept_current_user_terms(
    payload: TermsAcceptanceRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    if not payload.accepted:
        raise HTTPException(status_code=400, detail="Aceite do termo de uso obrigatorio")
    if payload.privacy_accepted is False:
        raise HTTPException(
            status_code=400,
            detail="Aceite da politica de privacidade obrigatorio",
        )
    return accept_terms_for_user(
        user_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@app.get("/users/me/export")
def export_current_user_data(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    return export_current_user(user_id)


@app.delete("/users/me")
def delete_current_user_account(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    return delete_current_user(user_id)


@app.post("/users/me/upload-cv")
def upload_cv(
    file: UploadFile = File(...),
    user_id: str = Depends(_require_terms_accepted),
) -> dict[str, Any]:
    return save_user_cv(file, user_id)


@app.post("/users/me/profile")
def upsert_profile(
    profile: UserProfileRequest,
    user_id: str = Depends(_require_terms_accepted),
) -> dict[str, Any]:
    profile_payload = _build_profile_payload(profile)
    if not profile_payload:
        raise HTTPException(status_code=400, detail="Perfil nao pode ser vazio")

    return save_user_profile(profile_payload, user_id)


@app.get("/users/me/profile")
def read_profile(user_id: str = Depends(_require_terms_accepted)) -> dict[str, Any]:
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


@app.get("/billing/me")
def read_billing_entitlement(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    return get_entitlement(user_id)


@app.post("/billing/checkout")
def create_billing_checkout(
    user_id: str = Depends(_require_terms_accepted),
) -> dict[str, str]:
    return create_checkout_session(user_id)


@app.post("/billing/webhook")
async def stripe_billing_webhook(request: Request) -> dict[str, Any]:
    payload = await request.body()
    return handle_stripe_webhook(payload, request.headers.get("stripe-signature"))


@app.post("/users/me/manual-profile")
def create_manual_profile(
    profile: ManualProfileRequest,
    user_id: str = Depends(require_llm_quota("manual_profile")),
) -> dict[str, Any]:
    summary_or_goal = " ".join(
        value.strip()
        for value in (profile.resumo_profissional, profile.objetivos_profissionais)
        if value and value.strip()
    )
    if len(summary_or_goal) < 30:
        raise HTTPException(
            status_code=400,
            detail="Informe um resumo profissional ou objetivo com pelo menos 30 caracteres",
        )
    if not profile.formacoes and not profile.experiencias:
        raise HTTPException(
            status_code=400,
            detail="Informe pelo menos uma formacao ou experiencia profissional",
        )
    for education in profile.formacoes:
        if not education.instituicao.strip() or not education.curso.strip():
            raise HTTPException(
                status_code=400,
                detail="Informe instituicao e curso em todas as formacoes",
            )
    for experience in profile.experiencias:
        if not experience.empresa.strip() or not experience.cargo.strip():
            raise HTTPException(
                status_code=400,
                detail="Informe empresa e cargo em todas as experiencias",
            )
        if len(experience.atividades.strip()) < 20:
            raise HTTPException(
                status_code=400,
                detail="Descreva as principais atividades da experiencia com pelo menos 20 caracteres",
            )

    payload = profile.model_dump(exclude_none=True)
    saved = save_manual_profile(payload, user_id)
    embeddings = rebuild_vectorstore_for_user(user_id)
    return {**saved, "embeddings": embeddings, "ready_for_analysis": True}


@app.get("/users/me/status")
def read_user_status(user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    cv_file = get_user_cv_file(user_id)
    chroma_dir = get_user_chroma_dir(user_id)
    output_dir = get_user_output_dir(user_id)
    profile = get_user_profile(user_id)
    has_cv = cv_file.exists() or get_latest_user_document_id(user_id) is not None
    has_embeddings = (
        count_embedding_chunks(user_id) > 0
        if PERSISTENCE_BACKEND == "mongodb"
        else chroma_dir.exists() and any(chroma_dir.iterdir())
    )

    return {
        "user_id": user_id,
        "has_cv": has_cv,
        "has_profile": profile is not None,
        "has_embeddings": has_embeddings,
        "generated_files": max(
            count_generated_files(user_id),
            len([item for item in output_dir.iterdir() if item.is_file()]),
        ),
    }


@app.post("/users/me/rebuild-embeddings")
def rebuild_embeddings(
    user_id: str = Depends(require_llm_quota("embeddings")),
) -> dict[str, Any]:
    return rebuild_vectorstore_for_user(user_id)


@app.get("/users/me/files/{file_name}")
def download_user_file(
    file_name: str,
    user_id: str = Depends(_require_terms_accepted),
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


@app.get("/users/me/gap-history")
def read_gap_history(
    user_id: str = Depends(_require_terms_accepted),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return {
        "items": list_job_analysis_insights(user_id, limit=limit, offset=offset),
        "limit": limit,
        "offset": offset,
    }


@app.post("/users/me/development-plan/generate")
def generate_user_development_plan(
    payload: DevelopmentPlanGenerateRequest,
    user_id: str = Depends(require_llm_quota("development_plan")),
) -> dict[str, Any]:
    return generate_development_plan(
        user_id=user_id,
        limit=payload.limit,
        replace_active=payload.replace_active,
    )


@app.get("/users/me/development-plan/active")
def read_user_active_development_plan(
    user_id: str = Depends(_require_terms_accepted),
) -> dict[str, Any]:
    plan = read_active_development_plan(user_id)
    return {
        "exists": plan is not None,
        "plan": plan,
    }


@app.get("/users/me/development-plans")
def read_user_development_plans(
    user_id: str = Depends(_require_terms_accepted),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return {
        "items": read_development_plan_history(
            user_id=user_id,
            limit=limit,
            offset=offset,
        ),
        "limit": limit,
        "offset": offset,
    }


@app.patch("/users/me/development-plan/{pdi_id}/items/{item_id}")
def update_user_development_plan_item(
    pdi_id: str,
    item_id: str,
    payload: DevelopmentPlanItemStatusRequest,
    user_id: str = Depends(_require_terms_accepted),
) -> dict[str, Any]:
    return update_development_plan_item_status(
        user_id=user_id,
        pdi_id=pdi_id,
        item_id=item_id,
        status=payload.status,
    )


@app.post("/processar")
def processar(
    request_data: RequestData,
    request: Request,
    user_id: str = Depends(require_llm_quota("processar")),
) -> dict[str, Any]:
    texto_entrada = request_data.texto.strip()
    if not texto_entrada:
        raise HTTPException(status_code=400, detail="Texto nao pode ser vazio")

    try:
        pipeline_result = pipeline_with_details(texto_entrada, user_id)
        resposta_usuario = str(pipeline_result["resposta_usuario"])
        match_score = _safe_int(pipeline_result.get("match_score"))

        if not bool(pipeline_result.get("should_generate_curriculum", True)):
            processing_run_id = create_processing_run(
                {
                    "user_id": user_id,
                    "input_text": texto_entrada,
                    "job_data": pipeline_result.get("vaga"),
                    "matching": pipeline_result.get("matching"),
                    "optimization": pipeline_result.get("otimizacao"),
                    "response_text": resposta_usuario,
                    "status": "completed",
                    "error_message": None,
                    "completed_at": _utc_now_iso(),
                },
            )
            _create_gap_history_from_pipeline(
                user_id=user_id,
                processing_run_id=processing_run_id,
                input_text=texto_entrada,
                pipeline_result=pipeline_result,
                match_score=match_score,
                status="completed",
                generation_blocked=True,
                blocked_reason="low_match_score",
            )
            return {
                "texto_resposta": resposta_usuario,
                "pdf_url": None,
                "user_id": user_id,
                "match_score": match_score,
                "pdf_generated": False,
                "generation_blocked": True,
                "blocked_reason": "low_match_score",
            }

        curriculo_otimizado = str(pipeline_result["curriculo"])
        nome_arquivo = f"{uuid.uuid4()}.pdf"
        caminho_pdf = get_user_output_dir(user_id) / nome_arquivo
        gerar_pdf_profissional(curriculo_otimizado, str(caminho_pdf))

        pdf_url = _build_public_file_url(request, nome_arquivo)
        processing_run_id = create_processing_run(
            {
                "user_id": user_id,
                "input_text": texto_entrada,
                "job_data": pipeline_result.get("vaga"),
                "matching": pipeline_result.get("matching"),
                "optimization": pipeline_result.get("otimizacao"),
                "response_text": resposta_usuario,
                "status": "completed",
                "error_message": None,
                "completed_at": _utc_now_iso(),
            },
        )
        create_generated_file(
            {
                "user_id": user_id,
                "processing_run_id": processing_run_id,
                "file_name": nome_arquivo,
                "file_path": str(caminho_pdf),
                "public_url": pdf_url,
                "media_type": "application/pdf",
                "bytes_size": caminho_pdf.stat().st_size if caminho_pdf.exists() else None,
            },
        )
        _create_gap_history_from_pipeline(
            user_id=user_id,
            processing_run_id=processing_run_id,
            input_text=texto_entrada,
            pipeline_result=pipeline_result,
            match_score=match_score,
            status="completed",
            generation_blocked=False,
            blocked_reason=None,
        )

        return {
            "texto_resposta": resposta_usuario,
            "pdf_url": pdf_url,
            "user_id": user_id,
            "match_score": match_score,
            "pdf_generated": True,
            "generation_blocked": False,
        }
    except HTTPException:
        raise
    except Exception as exc:
        create_processing_run(
            {
                "user_id": user_id,
                "input_text": texto_entrada,
                "job_data": None,
                "matching": None,
                "optimization": None,
                "response_text": None,
                "status": "failed",
                "error_message": str(exc),
                "completed_at": _utc_now_iso(),
            },
        )
        raise HTTPException(
            status_code=500,
            detail="Erro interno ao processar a vaga",
        ) from exc


@app.post("/users/me/cover-letter")
def generate_user_cover_letter(
    payload: CoverLetterRequest,
    request: Request,
    user_id: str = Depends(require_llm_quota("cover_letter")),
) -> dict[str, str]:
    empresa = payload.empresa.strip()
    if not empresa:
        raise HTTPException(status_code=400, detail="Nome da empresa nao pode ser vazio")

    try:
        carta = generate_cover_letter(empresa, user_id)

        nome_arquivo = f"carta-apresentacao-{uuid.uuid4()}.pdf"
        caminho_pdf = get_user_output_dir(user_id) / nome_arquivo
        gerar_pdf_carta_apresentacao(carta, str(caminho_pdf))

        pdf_url = _build_public_file_url(request, nome_arquivo)
        processing_run_id = create_processing_run(
            {
                "user_id": user_id,
                "input_text": f"Carta de apresentacao para {empresa}",
                "job_data": {"empresa": empresa},
                "matching": None,
                "optimization": None,
                "response_text": carta,
                "status": "completed",
                "error_message": None,
                "completed_at": _utc_now_iso(),
            },
        )
        create_generated_file(
            {
                "user_id": user_id,
                "processing_run_id": processing_run_id,
                "file_name": nome_arquivo,
                "file_path": str(caminho_pdf),
                "public_url": pdf_url,
                "media_type": "application/pdf",
                "bytes_size": caminho_pdf.stat().st_size if caminho_pdf.exists() else None,
            },
        )

        return {
            "texto_resposta": carta,
            "pdf_url": pdf_url,
            "user_id": user_id,
        }
    except HTTPException:
        raise
    except Exception as exc:
        create_processing_run(
            {
                "user_id": user_id,
                "input_text": f"Carta de apresentacao para {empresa}",
                "job_data": {"empresa": empresa},
                "matching": None,
                "optimization": None,
                "response_text": None,
                "status": "failed",
                "error_message": str(exc),
                "completed_at": _utc_now_iso(),
            },
        )
        raise HTTPException(
            status_code=500,
            detail="Erro interno ao gerar carta de apresentacao",
        ) from exc


def _build_public_file_url(request: Request, file_name: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/users/me/files/{file_name}"

    return str(request.url_for("download_user_file", file_name=file_name))


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _create_gap_history_from_pipeline(
    *,
    user_id: str,
    processing_run_id: int | str,
    input_text: str,
    pipeline_result: dict[str, Any],
    match_score: int,
    status: str,
    generation_blocked: bool,
    blocked_reason: str | None,
) -> None:
    vaga = pipeline_result.get("vaga")
    matching = pipeline_result.get("matching")
    create_job_analysis_insight(
        {
            "user_id": user_id,
            "processing_run_id": processing_run_id,
            "job_title": _extract_job_title(vaga),
            "company_name": _extract_text_field(vaga, "empresa"),
            "job_summary": _summarize_text(input_text),
            "match_score": match_score,
            "strengths": _extract_list_field(matching, "pontos_fortes"),
            "critical_gaps": _extract_list_field(matching, "gaps_criticos"),
            "matching_skills": _extract_list_field(matching, "matching_skills"),
            "missing_skills": _extract_list_field(matching, "missing_skills"),
            "status": status,
            "generation_blocked": generation_blocked,
            "blocked_reason": blocked_reason,
            "source": "processar",
            "created_at": _utc_now_iso(),
        },
    )


def _extract_job_title(vaga: Any) -> str | None:
    for field_name in ("cargo", "titulo", "title", "job_title", "nivel"):
        value = _extract_text_field(vaga, field_name)
        if value and value != "Nao informado":
            return value
    return None


def _extract_text_field(source: Any, field_name: str) -> str | None:
    if isinstance(source, dict):
        value = source.get(field_name)
    else:
        value = getattr(source, field_name, None)

    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _extract_list_field(source: Any, field_name: str) -> list[str]:
    if isinstance(source, dict):
        value = source.get(field_name)
    else:
        value = getattr(source, field_name, None)

    if not isinstance(value, list):
        return []

    return [str(item).strip() for item in value if str(item).strip()]


def _summarize_text(text: str, max_length: int = 500) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3].rstrip() + "..."
