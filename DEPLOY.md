# Deploy da API

Este guia descreve o deploy da API `meu_agente_de_emprego` em producao pequena, com foco no Render.

## Estado atual recomendado

O backend atual e adequado para:

- homologacao
- producao pequena
- ate cerca de 5 usuarios

Para esse cenario, a maior preocupacao nao e escala de CPU e sim:

- persistencia do `storage/` em local/dev e object storage R2/S3 em producao
- segredo JWT seguro
- CORS restrito ao dominio real do app

## Stack atual

- FastAPI
- Uvicorn
- JWT assinado com HMAC SHA-256
- armazenamento de arquivos:
  - local/dev: `storage/users/{user_id}/...` quando env S3 estiver ausente
  - producao: Cloudflare R2 (S3-compativel), keys `users/{user_id}/...`, bucket privado
  - embeddings locais em `storage/users/{user_id}/chroma/` (MongoDB em producao)

## Variaveis de ambiente obrigatorias

- `OPENAI_API_KEY`
- `ENVIRONMENT=production` (alias aceito: `ENV=production`)
- `AUTH_MODE=jwt`
- `JWT_SECRET`
- `CORS_ALLOW_ORIGINS`
- `MONGODB_URI`
- `MONGODB_DATABASE`
- `S3_ENDPOINT`
- `S3_BUCKET`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`

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
- `S3_REGION` (padrao `auto` no R2)
- `S3_SIGNED_URL_EXPIRES` (maximo 900 segundos / 15 min)

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
S3_ENDPOINT=https://<accountid>.r2.cloudflarestorage.com
S3_BUCKET=meu-agente-de-emprego
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
S3_REGION=auto
S3_SIGNED_URL_EXPIRES=900
```

## Validacoes de seguranca no startup

Quando `ENVIRONMENT=production`, a API agora falha ao iniciar se:

- `JWT_SECRET` estiver como `dev-secret-change-me`
- `CORS_ALLOW_ORIGINS` estiver vazio ou com `*`
- `MONGODB_URI` nao estiver configurada
- `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY_ID` ou `S3_SECRET_ACCESS_KEY` estiverem ausentes

Isso evita deploy acidental com configuracao insegura. Em local/dev, sem env S3, o fallback continua em disco.

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

PDFs gerados e uploads de curriculo devem ir para o object storage R2/S3 (`S3_*`) para sobreviver a redeploy. O banco guarda metadados + `object_key`. Download e autenticado (so o dono) via proxy em `GET /users/me/files/{nome}` ou signed URL de no maximo 15 minutos. O bucket permanece privado.

Sem object storage remoto em producao a API recusa subir. Em local/dev, sem env S3, os arquivos continuam em `STORAGE_DIR`.

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
6. Confirmar que `S3_ENDPOINT`, `S3_BUCKET` e as chaves R2 estao configuradas

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
