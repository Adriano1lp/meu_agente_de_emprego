# Deploy da API

Este guia descreve o deploy da API `meu_agente_de_emprego` em producao pequena, com foco no Render.

## Estado atual recomendado

O backend atual e adequado para:

- homologacao
- producao pequena
- ate cerca de 5 usuarios

Para esse cenario, a maior preocupacao nao e escala de CPU e sim:

- persistencia do `storage/`
- segredo JWT seguro
- CORS restrito ao dominio real do app

## Stack atual

- FastAPI
- Uvicorn
- JWT assinado com HMAC SHA-256
- armazenamento local em disco para:
  - contas em `storage/auth/users.json`
  - curriculos em `storage/users/{user_id}/documents/`
  - embeddings em `storage/users/{user_id}/chroma/`
  - PDFs em `storage/users/{user_id}/outputs/`

## Variaveis de ambiente obrigatorias

- `OPENAI_API_KEY`
- `ENVIRONMENT=production`
- `AUTH_MODE=jwt`
- `JWT_SECRET`
- `CORS_ALLOW_ORIGINS`
- `MONGODB_URI`
- `MONGODB_DATABASE`

## Variaveis de ambiente fortemente recomendadas

- `PUBLIC_BASE_URL`
- `STORAGE_DIR`
- `JWT_EXPIRATION_MINUTES`
- `MAX_UPLOAD_SIZE_MB`
- `OPENAI_CHAT_MODEL`
- `OPENAI_EMBEDDING_MODEL`
- `STRIPE_SECRET_KEY` (pode faltar em teste; sem ela o checkout retorna 503)
- `STRIPE_PRICE_ESSENCIAL`
- `STRIPE_WEBHOOK_SECRET` (pode faltar em teste; sem ela o webhook retorna 503 e nao processa)
- `STRIPE_CHECKOUT_SUCCESS_URL`
- `STRIPE_CHECKOUT_CANCEL_URL`

## Exemplo de configuracao para Render

```env
ENVIRONMENT=production
AUTH_MODE=jwt
OPENAI_API_KEY=sk-...
JWT_SECRET=troque-por-um-segredo-forte-e-longo
JWT_EXPIRATION_MINUTES=10080
PUBLIC_BASE_URL=https://sua-api.onrender.com
CORS_ALLOW_ORIGINS=https://seu-app.onrender.com
STORAGE_DIR=/opt/render/project/src/storage
PERSISTENCE_BACKEND=mongodb
MONGODB_URI=mongodb+srv://usuario:senha@cluster.mongodb.net/?retryWrites=true&w=majority
MONGODB_DATABASE=analista_de_vagas
MAX_UPLOAD_SIZE_MB=10
OPENAI_CHAT_MODEL=gpt-4o
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
STRIPE_SECRET_KEY=sk_live_...
STRIPE_PRICE_ESSENCIAL=price_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_CHECKOUT_SUCCESS_URL=https://seu-app.onrender.com/?billing=success
STRIPE_CHECKOUT_CANCEL_URL=https://seu-app.onrender.com/?billing=cancel
```

## Validacoes de seguranca no startup

Quando `ENVIRONMENT=production`, a API agora falha ao iniciar se:

- `JWT_SECRET` estiver como `dev-secret-change-me`
- `CORS_ALLOW_ORIGINS` estiver vazio ou com `*`
- `MONGODB_URI` nao estiver configurada

Isso evita deploy acidental com configuracao insegura.

## Render

## 1. Tipo de servico

Use um `Web Service`.

## 2. Build command

Se estiver usando o repo direto:

```bash
pip install -r requirements.txt
```

Se estiver usando Docker, o `Dockerfile` atual ja sobe a API com:

```bash
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers
```

## 3. Start command

Sem Docker:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT --proxy-headers
```

Nao use `--reload` em producao.

## 4. Persistencia

Atencao: o filesystem do Render pode ser efemero. A API agora deve usar MongoDB em producao para persistir:

- contas
- perfis
- metadados e texto extraido dos curriculos
- embeddings e chunks de contexto
- registros de processamento

Os PDFs gerados ainda sao arquivos locais em `storage/users/{user_id}/outputs/`. Se precisar manter downloads depois de redeploy/restart, configure disco persistente ou mova os PDFs para storage externo.

Sem MongoDB, voce ainda pode perder:

- PDFs gerados

## 5. Dominio publico

Defina:

```env
PUBLIC_BASE_URL=https://sua-api.onrender.com
```

Isso ajuda a API a montar URLs consistentes para os PDFs gerados.

## 6. CORS do app web

Defina apenas o dominio real do frontend:

```env
CORS_ALLOW_ORIGINS=https://seu-app.onrender.com
```

Se tiver mais de um dominio:

```env
CORS_ALLOW_ORIGINS=https://seu-app.onrender.com,https://www.seu-app.com
```

## Rodando localmente

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Docker local

```bash
docker build -t analista-vagas-api .
docker run --env-file .env -p 8000:8000 analista-vagas-api
```

## Checklist de release

Antes de publicar:

1. Confirmar que `ENVIRONMENT=production`
2. Confirmar que `JWT_SECRET` nao esta no valor padrao
3. Confirmar que `CORS_ALLOW_ORIGINS` nao esta com `*`
4. Confirmar que `OPENAI_API_KEY` esta configurada
5. Confirmar que `PUBLIC_BASE_URL` aponta para a URL real da API
6. Confirmar que `STORAGE_DIR` esta em local persistente ou aceitar conscientemente o risco

## Checklist de smoke test

Depois do deploy:

1. `GET /health`
2. `POST /auth/register`
3. `POST /auth/login`
4. `GET /auth/me`
5. `POST /users/me/upload-cv`
6. `POST /users/me/rebuild-embeddings`
7. `GET /users/me/status`
8. `GET /billing/me`
9. `POST /processar` (Free: 5/mes UTC; Essencial ativo: 30/mes UTC; 402 se cota esgotada)
10. `GET /users/me/files/{nome_do_arquivo}`

## Exemplos de smoke test

### Health

```bash
curl https://sua-api.onrender.com/health
```

### Register

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d "{\"display_name\":\"Teste\",\"email\":\"teste@example.com\",\"password\":\"senha-forte-123\"}" \
  https://sua-api.onrender.com/auth/register
```

### Login

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"teste@example.com\",\"password\":\"senha-forte-123\"}" \
  https://sua-api.onrender.com/auth/login
```

### Auth me

```bash
curl \
  -H "Authorization: Bearer <jwt>" \
  https://sua-api.onrender.com/auth/me
```

## Observacoes operacionais

- Para o tamanho atual do projeto, `users.json` e `storage/` funcionam, mas nao sao o formato ideal para crescer.
- Se o numero de usuarios subir ou se a persistencia do Render ficar limitada, o proximo passo natural e migrar contas e metadados para banco.
- O app Flutter atual ja consome a API com JWT, entao o deploy deve usar `AUTH_MODE=jwt`.
