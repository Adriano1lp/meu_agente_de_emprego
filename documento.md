# Documentacao dos Endpoints da API

Este documento descreve os endpoints atualmente disponiveis na API `meu_agente_de_emprego`, o fluxo de autenticacao implementado e o comportamento esperado de cada rota.

## Base URL local

```bash
http://localhost:8000
```

## Visao geral da autenticacao

O fluxo principal agora e baseado em JWT.

1. O cliente cria conta em `POST /auth/register` ou entra em `POST /auth/login`
2. A API devolve `access_token`
3. O cliente envia esse token nos endpoints protegidos usando:

```http
Authorization: Bearer <token>
```

4. Para restaurar a sessao em outro dispositivo ou depois de reinstalar o app, o cliente reutiliza o token salvo e chama:

```http
GET /auth/me
```

## Compatibilidade com header simples

O backend ainda mantem suporte ao header legado `X-User-Id` quando `AUTH_MODE` nao estiver configurado como `jwt`. Porem o fluxo recomendado e o fluxo com JWT.

## Token JWT

O token emitido pela API:

- e assinado com HMAC SHA-256
- carrega `sub`, `user_id`, `email`, `display_name`, `iat` e `exp`
- respeita `JWT_EXPIRATION_MINUTES`

Se o token estiver ausente, expirado ou com assinatura invalida, a API responde com `401`.

## 1. Health check

### `GET /health`

Usado para verificar se a API esta respondendo.

### Exemplo

```bash
curl http://localhost:8000/health
```

### Resposta esperada

```json
{
  "status": "ok"
}
```

## 2. Criar conta

### `POST /auth/register`

Cria um novo usuario persistido no servidor e devolve um token JWT de acesso.

### Body esperado

```json
{
  "display_name": "Adriano Lima",
  "email": "adriano@email.com",
  "password": "senha-forte-123"
}
```

### Exemplo

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d "{\"display_name\":\"Adriano Lima\",\"email\":\"adriano@email.com\",\"password\":\"senha-forte-123\"}" \
  http://localhost:8000/auth/register
```

### Resposta esperada

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "user": {
    "user_id": "user_123abc456def",
    "email": "adriano@email.com",
    "display_name": "Adriano Lima",
    "created_at": "2026-04-27T18:30:00+00:00",
    "updated_at": "2026-04-27T18:30:00+00:00"
  }
}
```

### Erros comuns

- `400`: nome obrigatorio
- `400`: email invalido
- `400`: senha com menos de 8 caracteres
- `409`: email ja cadastrado

## 3. Entrar na conta

### `POST /auth/login`

Autentica um usuario existente e devolve um token JWT de acesso.

### Body esperado

```json
{
  "email": "adriano@email.com",
  "password": "senha-forte-123"
}
```

### Exemplo

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"adriano@email.com\",\"password\":\"senha-forte-123\"}" \
  http://localhost:8000/auth/login
```

### Resposta esperada

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "user": {
    "user_id": "user_123abc456def",
    "email": "adriano@email.com",
    "display_name": "Adriano Lima",
    "created_at": "2026-04-27T18:30:00+00:00",
    "updated_at": "2026-04-27T18:30:00+00:00"
  }
}
```

### Erros comuns

- `401`: email ou senha invalidos

## 4. Restaurar sessao

### `GET /auth/me`

Retorna os dados do usuario autenticado a partir do token atual. Esse endpoint e o principal para recuperar a conta em outro dispositivo ou depois de reinstalar o app.

### Exemplo

```bash
curl \
  -H "Authorization: Bearer <jwt>" \
  http://localhost:8000/auth/me
```

### Resposta esperada

```json
{
  "user_id": "user_123abc456def",
  "email": "adriano@email.com",
  "display_name": "Adriano Lima",
  "created_at": "2026-04-27T18:30:00+00:00",
  "updated_at": "2026-04-27T18:30:00+00:00"
}
```

### Erros comuns

- `401`: header `Authorization` ausente
- `401`: token expirado
- `401`: assinatura JWT invalida
- `404`: usuario nao encontrado

## 5. Usuario autenticado

### `GET /users/me`

Retorna o `user_id` resolvido pela autenticacao atual e, quando existir, tambem `email` e `display_name`.

### Exemplo

```bash
curl \
  -H "Authorization: Bearer <jwt>" \
  http://localhost:8000/users/me
```

### Resposta esperada

```json
{
  "user_id": "user_123abc456def",
  "auth_mode": "jwt",
  "email": "adriano@email.com",
  "display_name": "Adriano Lima"
}
```

## 6. Upload de curriculo

### `POST /users/me/upload-cv`

Recebe o curriculo do usuario autenticado e salva:

- o arquivo original em `storage/users/{user_id}/documents/`
- o texto extraido em `storage/users/{user_id}/documents/cv.txt`

### Formatos aceitos

- `.txt`
- `.pdf`

### Exemplo

```bash
curl -X POST \
  -H "Authorization: Bearer <jwt>" \
  -F "file=@cv.txt" \
  http://localhost:8000/users/me/upload-cv
```

### Resposta esperada

```json
{
  "user_id": "user_123abc456def",
  "filename": "cv.txt",
  "content_type": "text/plain",
  "bytes_received": 24,
  "updated_at": "2026-04-27T18:35:00+00:00",
  "cv_file": "C:\\Projetos\\analista_de_vagas\\meu_agente_de_emprego\\storage\\users\\user_123abc456def\\documents\\cv.txt",
  "original_file": "C:\\Projetos\\analista_de_vagas\\meu_agente_de_emprego\\storage\\users\\user_123abc456def\\documents\\cv_original.txt"
}
```

### Erros comuns

- `400`: arquivo vazio
- `400`: formato invalido
- `400`: arquivo acima do limite `MAX_UPLOAD_SIZE_MB`
- `500`: tentativa de upload de PDF sem `pypdf` instalado

## 7. Salvar perfil do usuario

### `POST /users/me/profile`

Salva dados complementares do usuario em JSON.

O perfil atual fica em:

- `storage/users/{user_id}/profile.json`

O historico de versoes fica em:

- `storage/users/{user_id}/profile_versions.jsonl`

### Campos aceitos

- `nome_completo`
- `email`
- `telefone`
- `linkedin`
- `resumo_profissional`
- `habilidades`
- `idiomas`
- `links`
- `objetivos`
- `experiencias`
- `formacao`

Tambem sao aceitos campos extras, porque o model atual esta com `extra="allow"`.

### Exemplo

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <jwt>" \
  -d "{\"nome_completo\":\"Adriano Lima\",\"habilidades\":[\"Python\",\"QA\"]}" \
  http://localhost:8000/users/me/profile
```

### Resposta esperada

```json
{
  "user_id": "user_123abc456def",
  "version": 1,
  "updated_at": "2026-04-27T18:40:00+00:00",
  "profile": {
    "nome_completo": "Adriano Lima",
    "habilidades": ["Python", "QA"],
    "idiomas": [],
    "links": [],
    "objetivos": [],
    "experiencias": [],
    "formacao": []
  }
}
```

### Observacao importante

O endpoint versiona corretamente, mas o perfil atual e sobrescrito com o payload enviado na ultima chamada. Ainda nao existe merge automatico entre versoes.

## 8. Ler perfil do usuario

### `GET /users/me/profile`

Retorna o perfil atual do usuario autenticado.

### Exemplo

```bash
curl \
  -H "Authorization: Bearer <jwt>" \
  http://localhost:8000/users/me/profile
```

### Resposta quando ainda nao existe perfil

```json
{
  "user_id": "user_123abc456def",
  "exists": false,
  "profile": null
}
```

### Resposta quando ja existe perfil

```json
{
  "user_id": "user_123abc456def",
  "exists": true,
  "version": 2,
  "updated_at": "2026-04-27T18:45:00+00:00",
  "profile": {
    "nome_completo": "Adriano Lima",
    "habilidades": ["Python", "QA", "IA"],
    "idiomas": [],
    "links": [],
    "objetivos": [],
    "experiencias": [],
    "formacao": []
  }
}
```

## 9. Gerar embeddings do usuario

### `POST /users/me/rebuild-embeddings`

Le o `cv.txt` do usuario, quebra o curriculo em chunks e persiste os embeddings em:

- `storage/users/{user_id}/chroma/`

Esse endpoint deve ser chamado depois do upload do curriculo.

### Exemplo

```bash
curl -X POST \
  -H "Authorization: Bearer <jwt>" \
  http://localhost:8000/users/me/rebuild-embeddings
```

### Resposta esperada

```json
{
  "user_id": "user_123abc456def",
  "chunks": 1,
  "processed_at": "2026-04-27T18:50:00+00:00",
  "embedding_model": "text-embedding-3-small",
  "chroma_dir": "C:\\Projetos\\analista_de_vagas\\meu_agente_de_emprego\\storage\\users\\user_123abc456def\\chroma",
  "cv_file": "C:\\Projetos\\analista_de_vagas\\meu_agente_de_emprego\\storage\\users\\user_123abc456def\\documents\\cv.txt"
}
```

### Erros comuns

- `400`: curriculo ainda nao enviado
- `400`: nao foi possivel gerar chunks validos
- `500`: falha de integracao com OpenAI ou armazenamento vetorial

## 10. Status do usuario

### `GET /users/me/status`

Retorna um resumo rapido do estado dos dados do usuario autenticado.

### Exemplo

```bash
curl \
  -H "Authorization: Bearer <jwt>" \
  http://localhost:8000/users/me/status
```

### Resposta esperada

```json
{
  "user_id": "user_123abc456def",
  "has_cv": true,
  "has_profile": true,
  "has_embeddings": true,
  "generated_files": 2
}
```

## 11. Processar uma vaga

### `POST /processar`

Recebe o texto de uma vaga e tenta:

- analisar a vaga
- comparar com o contexto do candidato
- gerar uma resposta explicativa
- gerar um PDF de curriculo otimizado

### Exemplo

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <jwt>" \
  -d "{\"texto\":\"Vaga para desenvolvedor Python com FastAPI e AWS\"}" \
  http://localhost:8000/processar
```

### Body esperado

```json
{
  "texto": "Descricao completa da vaga"
}
```

### Resposta esperada

```json
{
  "texto_resposta": "Resumo do fit do candidato com a vaga...",
  "pdf_url": "http://localhost:8000/users/me/files/arquivo.pdf",
  "user_id": "user_123abc456def"
}
```

### Erros comuns

- `400`: `texto` vazio
- `400`: embeddings do usuario ainda nao gerados
- `500`: erro interno ao processar LLM, embeddings ou geracao de PDF

## 12. Download de arquivos do usuario

### `GET /users/me/files/{nome_do_arquivo}`

Serve os PDFs gerados pelo proprio usuario autenticado.

### Exemplo

```bash
curl \
  -H "Authorization: Bearer <jwt>" \
  http://localhost:8000/users/me/files/arquivo.pdf \
  --output curriculo.pdf
```

### Regras importantes

- o arquivo precisa existir dentro de `storage/users/{user_id}/outputs/`
- um usuario nao consegue baixar o arquivo de outro usuario
- o endpoint retorna `404` quando o arquivo nao pertence ao usuario autenticado

## Fluxo recomendado de uso

1. `GET /health`
2. `POST /auth/register` ou `POST /auth/login`
3. `GET /auth/me`
4. `GET /users/me/status`
5. `POST /users/me/upload-cv`
6. `POST /users/me/rebuild-embeddings`
7. `GET /users/me/status`
8. `POST /processar`
9. `GET /users/me/files/{nome_do_arquivo}`

## Recuperacao de usuario em outro dispositivo

O fluxo correto de recuperacao agora e:

1. o usuario entra novamente com `email` e `senha`
2. o app recebe um novo `access_token`
3. o app consulta `GET /auth/me`
4. o app consulta `GET /users/me/status`
5. se `has_cv` e `has_embeddings` forem `true`, o usuario pode voltar direto para a analise de vagas
6. se nao, o app solicita novo upload do curriculo

## Persistencia interna

### Contas

As contas autenticadas ficam persistidas em:

- `storage/auth/users.json`

Esse arquivo armazena:

- email normalizado
- `user_id`
- `display_name`
- hash de senha com PBKDF2 SHA-256
- datas de criacao e atualizacao

### Dados do usuario

Os dados de negocio continuam separados por `user_id` em:

- `storage/users/{user_id}/documents/`
- `storage/users/{user_id}/profile.json`
- `storage/users/{user_id}/chroma/`
- `storage/users/{user_id}/outputs/`

## Variaveis de ambiente relevantes

- `OPENAI_API_KEY`
- `OPENAI_CHAT_MODEL`
- `OPENAI_EMBEDDING_MODEL`
- `PUBLIC_BASE_URL`
- `CORS_ALLOW_ORIGINS`
- `AUTH_MODE`
- `JWT_SECRET`
- `JWT_EXPIRATION_MINUTES`
- `MAX_UPLOAD_SIZE_MB`
- `STORAGE_DIR`
