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

### Billing Stripe

Planos:

- **Free** — cota dura de LLM (`FREE_LLM_QUOTA_MONTHLY`, padrao 5/mes)
- **Essencial** — R$ 19,90/mes, cota maior (`ESSENCIAL_LLM_QUOTA_MONTHLY`, padrao 100/mes)

Env obrigatorias para cobrar de verdade:

- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_ESSENCIAL` (price id `price_...`)
- `STRIPE_SUCCESS_URL` / `STRIPE_CANCEL_URL` (opcionais; caem em `PUBLIC_BASE_URL`)

Crie o produto/preco no Stripe Dashboard ou:

```bash
python scripts/create_stripe_essencial_price.py
```

No Stripe, aponte o webhook para `POST /billing/webhook` (eventos `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`).

Fluxo do app:

1. `GET /billing/me` — plano, cota e uso do mes
2. `POST /billing/checkout` — sessao de checkout do Essencial
3. Stripe chama `/billing/webhook` (assinatura verificada) e ativa a assinatura

`POST /processar` e os outros endpoints de LLM sao bloqueados no servidor com `402` quando a cota acaba.

### Direitos do titular (LGPD)

- `GET /users/me/export` — JSON com os dados do usuario autenticado
- `DELETE /users/me` — anonimiza a conta (`deleted_at`), apaga registros relacionados e remove arquivos em `storage/users/{user_id}/`

### Reset de senha

`POST /auth/password-reset/request` ainda nao envia email real. Em nao-producao o token pode ser devolvido na resposta (`PASSWORD_RESET_EXPOSE_TOKEN`). Envio por email fica para depois.

## Testes

```bash
pytest
```
