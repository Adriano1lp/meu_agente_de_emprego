# Meu Agente de Emprego — API

API FastAPI do app Meu Agente de Emprego. Persistencia em SQLite (dev) ou MongoDB (producao).

## Setup local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Variaveis de ambiente

Veja `.env.example` e `DEPLOY.md`. Nao commite secrets.

### Obrigatorias em producao

- `OPENAI_API_KEY`
- `ENVIRONMENT=production`
- `AUTH_MODE=jwt`
- `JWT_SECRET` (nao use o valor padrao)
- `CORS_ALLOW_ORIGINS` (dominio real do app, nunca `*`)
- `MONGODB_URI`
- `MONGODB_DATABASE`

### Termos e privacidade (LGPD)

- `TERMS_OF_SERVICE_VERSION` (padrao `tos_v1`)
- `PRIVACY_POLICY_VERSION` (padrao `privacy_v1`)

`POST /auth/register` exige `terms_accepted=true` e `privacy_accepted=true`.
A API grava a versao + timestamp no usuario e um log append-only em `consent_log`.
Consulte as versoes vigentes em `GET /legal`.

Ao publicar uma nova versao dos documentos, atualize as env vars. Usuarios com versao antiga recebem `needs_reconsent=true` e devem chamar `POST /users/me/terms/accept`.

## Testes

```bash
pytest
```
