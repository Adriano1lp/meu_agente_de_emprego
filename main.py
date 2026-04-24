from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import APP_NAME, APP_VERSION, CORS_ALLOW_ORIGINS, OUTPUT_DIR, PUBLIC_BASE_URL
from services.main_chat import pipeline
from services.main_curriculo import gerar_pdf_profissional

app = FastAPI(title=APP_NAME, version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/files", StaticFiles(directory=str(OUTPUT_DIR)), name="files")


class RequestData(BaseModel):
    texto: str


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/processar")
def processar(request_data: RequestData, request: Request) -> dict[str, str]:
    texto_entrada = request_data.texto.strip()
    if not texto_entrada:
        raise HTTPException(status_code=400, detail="Texto nao pode ser vazio")

    try:
        curriculo_otimizado, resposta_usuario = pipeline(texto_entrada)

        nome_arquivo = f"{uuid.uuid4()}.pdf"
        caminho_pdf = OUTPUT_DIR / nome_arquivo
        gerar_pdf_profissional(curriculo_otimizado, str(caminho_pdf))

        pdf_url = _build_public_file_url(request, nome_arquivo)

        return {
            "texto_resposta": resposta_usuario,
            "pdf_url": pdf_url,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _build_public_file_url(request: Request, file_name: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/files/{file_name}"

    return str(request.url_for("files", path=file_name))
