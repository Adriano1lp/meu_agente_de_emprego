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
O body deve enviar os quatro campos de consentimento, com as versoes vigentes
(`CURRENT_TERMS_VERSION` e `CURRENT_PRIVACY_VERSION`, hoje `"1.0"`).
Sem os quatro campos, ou com versao diferente da vigente, a API retorna `400`
e nao cria o usuario. O signup grava duas linhas no `consent_log` (terms e privacy).

### Body esperado

```json
{
  "display_name": "Adriano Lima",
  "email": "adriano@email.com",
  "password": "senha-forte-123",
  "terms_accepted": true,
  "terms_version": "1.0",
  "privacy_accepted": true,
  "privacy_version": "1.0"
}
```

### Exemplo

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d "{\"display_name\":\"Adriano Lima\",\"email\":\"adriano@email.com\",\"password\":\"senha-forte-123\",\"terms_accepted\":true,\"terms_version\":\"1.0\",\"privacy_accepted\":true,\"privacy_version\":\"1.0\"}" \
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
    "terms_accepted": true,
    "terms_accepted_at": "2026-06-08T12:00:00+00:00",
    "terms_version": "1.0",
    "privacy_accepted": true,
    "privacy_accepted_at": "2026-06-08T12:00:00+00:00",
    "privacy_version": "1.0",
    "created_at": "2026-04-27T18:30:00+00:00",
    "updated_at": "2026-04-27T18:30:00+00:00"
  }
}
```

### Erros comuns

- `400`: nome obrigatorio
- `400`: email invalido
- `400`: senha com menos de 8 caracteres
- `400`: aceite de termos/privacidade ausente ou versao diferente da vigente
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
Tambem retorna `terms_*` e `privacy_*` (aceite, versao e timestamp).

Se `terms_version` ou `privacy_version` for diferente da vigente, rotas autenticadas
(exceto `POST /consent` e `GET /legal/*`) respondem `403` com `TERMS_OUTDATED` ou `PRIVACY_OUTDATED`.

### `GET /legal/terms` e `GET /legal/privacy`

Servem o markdown estatico da versao pedida. `404` se a versao nao existir.

```bash
curl "http://localhost:8000/legal/terms?version=1.0"
curl "http://localhost:8000/legal/privacy?version=1.0"
```

### `POST /consent`

Reaceite autenticado de um documento. Body: `{ "doc": "terms"|"privacy", "version": "1.0" }`.
A versao deve ser a vigente. A API faz append no `consent_log` e atualiza os campos no usuario.

```bash
curl -X POST \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d "{\"doc\":\"terms\",\"version\":\"1.0\"}" \
  http://localhost:8000/consent
```

### `POST /users/me/terms/accept`

Mantido por compatibilidade: registra o aceite da versao vigente de termos
(append no log + atualiza o usuario). Prefira `POST /consent`.

Body esperado:

```json
{
  "accepted": true
}
```

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

### `GET /users/me/export`

Exportacao LGPD dos dados do usuario autenticado. Resposta sincrona em JSON
(sem ZIP e sem binarios). O pacote nunca inclui senha, `password_hash` nem JWT.

Conteudo:

- conta: `email`, `display_name`, `terms_*`, `privacy_*`, `created_at`
- perfil estruturado, se existir
- historico de processamentos / candidaturas (`processing_runs`, insights, PDIs)
- metadados de arquivos (nome, tipo, `created_at`)

A rota usa o mesmo gate de consentimento da Fatia 1: `401` sem auth e `403`
com `TERMS_OUTDATED` ou `PRIVACY_OUTDATED`.

```bash
curl \
  -H "Authorization: Bearer <jwt>" \
  http://localhost:8000/users/me/export
```

### `DELETE /users/me`

Exclusao da conta (direito de eliminacao / purge). Exige autenticacao e o body
exato `{ "confirm": "DELETE" }`. Sem `confirm`, ou com texto diferente, a API
responde `400`.

Efeito:

- soft-delete com `deleted_at`
- scrub de PII: email vira `deleted+{user_id}@invalid.local`, nome vira
  `Conta excluida`, `password_hash` e invalidado
- apaga perfil, documentos, processamentos, embeddings, arquivos gerados e
  tokens de reset ligados ao `user_id`
- remove `storage/users/{user_id}/` (curriculo, chroma, PDFs)
- `consent_log` permanece append-only (doc, version, accepted_at, user_id)
- login com o email original falha (`401`); o email fica livre para um novo cadastro
  (o `user_id` da conta apagada permanece no stub; o recadastro recebe um id novo)
- rotas autenticadas do usuario passam a responder `401`

```bash
curl -X DELETE \
  -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d "{\"confirm\":\"DELETE\"}" \
  http://localhost:8000/users/me
```

Resposta esperada:

```json
{
  "user_id": "user_123abc456def",
  "deleted": true,
  "deleted_at": "2026-09-05T12:00:00+00:00"
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

Payloads vazios ou sem conteudo util sao rejeitados com status `400` e mensagem `Perfil nao pode ser vazio`.

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

### `POST /users/me/manual-profile`

Salva informacoes academicas e profissionais sem exigir upload de arquivo. A rota exige autenticacao e aceite dos termos, valida o conteudo, versiona o perfil, consolida um `cv.txt` rastreavel e reconstrói os embeddings.

Regras minimas:

- `resumo_profissional` e/ou `objetivos_profissionais` devem somar pelo menos 30 caracteres;
- deve existir ao menos um item em `formacoes` ou `experiencias`;
- cada formacao exige `instituicao` e `curso`;
- cada experiencia exige `empresa`, `cargo` e `atividades` com pelo menos 20 caracteres.

Exemplo:

```json
{
  "titulo_profissional": "Desenvolvedor backend",
  "resumo_profissional": "Profissional com experiencia em APIs e bancos de dados.",
  "formacoes": [
    {
      "instituicao": "Universidade Exemplo",
      "curso": "Ciencia da Computacao",
      "ano_conclusao": "2024",
      "status": "concluido"
    }
  ],
  "experiencias": [],
  "habilidades_tecnicas": ["Python", "SQL"],
  "idiomas": ["Ingles avancado"]
}
```

Resposta de sucesso inclui o perfil salvo, metadados do documento, resultado dos embeddings e `ready_for_analysis: true`.

### `POST /users/me/rebuild-embeddings`

Le o curriculo do usuario, quebra o texto em chunks e persiste os embeddings.

Em producao com MongoDB, os chunks e vetores ficam na colecao `embedding_chunks`.
Em fallback local com SQLite, os vetores continuam em `storage/users/{user_id}/chroma/`.

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
  "vector_store": "mongodb",
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

## 11. Billing Stripe (plano Essencial)

Cotas e assinatura sao avaliadas **no servidor**. Nesta fatia a cota vale apenas para `POST /processar`. Carta, PDI e embeddings nao consomem cota.

### Planos

| Plano | Preco | Cota de `POST /processar` | Entitlement |
| --- | --- | --- | --- |
| Free | — | **5** por mes civil UTC | sem assinatura `active` |
| Essencial | **R$ 19,90/mes** | **30** por mes civil UTC | Stripe `subscription_status=active` |

O contador e por `user_id` + `YYYY-MM` (UTC) e zera automaticamente no dia 1 de cada mes UTC. `past_due` nao conta como Essencial (grace 0 dias): a cota efetiva volta a 5. Cancelamento/exclusao da assinatura grava `plan=free` e cota 5.

### `GET /billing/me`

Autenticado + consentimento vigente. Devolve o entitlement efetivo.

```bash
curl -H "Authorization: Bearer <jwt>" http://localhost:8000/billing/me
```

```json
{
  "plan": "free",
  "subscription_status": "none",
  "used": 0,
  "limit": 5,
  "period": "2026-09",
  "remaining": 5
}
```

### `POST /billing/checkout`

Autenticado + consentimento vigente. Cria uma Stripe Checkout Session `mode=subscription` no preco `STRIPE_PRICE_ESSENCIAL`.

```bash
curl -X POST \
  -H "Authorization: Bearer <jwt>" \
  http://localhost:8000/billing/checkout
```

```json
{
  "checkout_url": "https://checkout.stripe.com/c/pay/cs_...",
  "session_id": "cs_..."
}
```

Sem `STRIPE_SECRET_KEY` ou `STRIPE_PRICE_ESSENCIAL` a API responde `503`. URLs de sucesso/cancelamento: `STRIPE_CHECKOUT_SUCCESS_URL` e `STRIPE_CHECKOUT_CANCEL_URL` (fallback em `PUBLIC_BASE_URL`).

### `POST /billing/webhook`

Publico. **Sempre** valida `Stripe-Signature` com `STRIPE_WEBHOOK_SECRET`. Eventos processados de forma idempotente (`event.id`):

- `checkout.session.completed`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.paid`
- `invoice.payment_failed`

O usuario persiste `stripe_customer_id`, `stripe_subscription_id`, `plan` (`free`|`essencial`) e `subscription_status` (`active`|`past_due`|`canceled`|`none`). Sem `STRIPE_WEBHOOK_SECRET` a rota responde `503` e nao processa o evento.

Nao ha Customer Portal nesta fatia.

### Como testar

1. Sem chaves Stripe: cadastre um usuario, chame `GET /billing/me` (limit 5) e `POST /processar` seis vezes — a sexta deve ser `402` com `code=SUBSCRIPTION_REQUIRED`.
2. Checkout local: defina `STRIPE_SECRET_KEY` e `STRIPE_PRICE_ESSENCIAL` (Price mensal BRL 1990 no Dashboard) e chame `POST /billing/checkout`.
3. Webhook: `stripe listen --forward-to localhost:8000/billing/webhook` com `STRIPE_WEBHOOK_SECRET` do CLI. Conclua o Checkout de teste; `GET /billing/me` deve mostrar `plan=essencial` e `limit=30`.
4. Reenviar o mesmo evento (mesmo `id`) deve responder `200` com `duplicate: true` e nao alterar o usuario.

Testes automatizados (Stripe mockado, sem rede):

```bash
pytest tests/test_billing.py
```

## 12. Processar uma vaga

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
  "user_id": "user_123abc456def",
  "match_score": 78,
  "pdf_generated": true,
  "generation_blocked": false
}
```

### Baixa adesao

Quando a adesao calculada em `match_score` for menor que 60, a API nao gera curriculo otimizado nem PDF. Isso evita consumo desnecessario de tokens em vagas com baixa compatibilidade.

Resposta esperada nesse caso:

```json
{
  "texto_resposta": "Sua adesao a esta vaga ficou em 42%...",
  "pdf_url": null,
  "user_id": "user_123abc456def",
  "match_score": 42,
  "pdf_generated": false,
  "generation_blocked": true,
  "blocked_reason": "low_match_score"
}
```

O app deve exibir `texto_resposta` normalmente e nao exibir botao de PDF quando `pdf_url` vier vazio ou nulo.

Cada chamada valida de `POST /processar` tambem registra um historico estruturado de pontos fortes e gaps do usuario, mesmo quando a geracao de PDF e bloqueada por baixa adesao.

### Erros comuns

- `400`: `texto` vazio (nao consome cota)
- `400`: embeddings do usuario ainda nao gerados
- `402`: cota do mes esgotada. Body em `detail`:
  `{ "code": "QUOTA_EXCEEDED"|"SUBSCRIPTION_REQUIRED", "message": "...", "used": 5, "limit": 5, "plan": "free" }`
  Free esgotado usa `SUBSCRIPTION_REQUIRED`; Essencial esgotado usa `QUOTA_EXCEEDED`.
- `500`: erro interno ao processar LLM, embeddings ou geracao de PDF

## 12. Historico de gaps do usuario

### `GET /users/me/gap-history`

Retorna o historico de pontos fortes, lacunas e habilidades identificadas nas analises de vaga do usuario autenticado.

### Query params

- `limit`: quantidade maxima de registros, entre 1 e 100. Padrao: 20.
- `offset`: deslocamento para paginacao simples. Padrao: 0.

### Exemplo

```bash
curl \
  -H "Authorization: Bearer <jwt>" \
  "http://localhost:8000/users/me/gap-history?limit=10"
```

### Resposta esperada

```json
{
  "items": [
    {
      "id": "1",
      "processing_run_id": 10,
      "created_at": "2026-06-04T10:00:00+00:00",
      "job_title": "Analista de Dados",
      "company_name": "ACME",
      "job_summary": "Vaga para Analista de Dados com SQL e Power BI",
      "match_score": 72,
      "strengths": ["SQL"],
      "critical_gaps": ["Power BI avancado"],
      "matching_skills": ["SQL"],
      "missing_skills": ["Power BI"],
      "status": "completed",
      "generation_blocked": false,
      "blocked_reason": null,
      "source": "processar"
    }
  ],
  "limit": 10,
  "offset": 0
}
```

### Regras importantes

- a rota e autenticada;
- cada usuario ve apenas seu proprio historico;
- os registros sao retornados do mais recente para o mais antigo;
- o historico e gravado tanto em baixa adesao quanto em alta adesao.

## 13. Download de arquivos do usuario

## 13. PDI do usuario

### `POST /users/me/development-plan/generate`

Gera um Plano de Desenvolvimento Individual com base nas ultimas analises salvas em `job_analysis_insights`.

### Body esperado

```json
{
  "limit": 10,
  "replace_active": true
}
```

### Resposta esperada

```json
{
  "pdi_id": "pdi_123",
  "title": "PDI para evoluir nos gaps das vagas analisadas",
  "main_objective": "Desenvolver dominio pratico em Power BI e Airflow...",
  "summary": "Plano criado a partir das ultimas vagas analisadas.",
  "priority_gaps": ["Power BI", "Airflow"],
  "strengths_to_leverage": ["Python", "SQL"],
  "progress_percent": 0,
  "status": "active",
  "sections": {
    "70": [],
    "20": [],
    "10": []
  },
  "checklist_items": []
}
```

### Regras importantes

- a rota e autenticada;
- usa apenas historico do usuario autenticado;
- exige pelo menos duas analises de vaga;
- `limit` aceita valores entre 1 e 20;
- se ja existir PDI ativo, enviar `replace_active=true` para substituir;
- os itens iniciam com status `pending` e progresso `0`.

### `GET /users/me/development-plan/active`

Retorna o PDI ativo do usuario.

```json
{
  "exists": true,
  "plan": {
    "pdi_id": "pdi_123",
    "progress_percent": 0
  }
}
```

### `GET /users/me/development-plans`

Retorna historico resumido de PDIs do usuario autenticado.

Query params:

- `limit`: entre 1 e 100. Padrao: 20.
- `offset`: deslocamento para paginacao. Padrao: 0.

### `PATCH /users/me/development-plan/{pdi_id}/items/{item_id}`

Atualiza o status de um item do checklist e recalcula o progresso.

Body esperado:

```json
{
  "status": "completed"
}
```

Status aceitos:

- `pending`
- `in_progress`
- `completed`

Quando todos os pesos forem concluidos, o PDI passa para `completed`. Se algum item for reaberto, volta para `active`.

## 14. Download de arquivos do usuario

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
4. `POST /consent` quando a API responder `403` com `TERMS_OUTDATED` ou `PRIVACY_OUTDATED`
5. `GET /users/me/status`
6. `POST /users/me/upload-cv`
7. `POST /users/me/rebuild-embeddings`
8. `GET /users/me/status`
9. `GET /billing/me`
10. `POST /processar`
11. `GET /users/me/gap-history`
12. `POST /users/me/development-plan/generate`
13. `GET /users/me/development-plan/active`
14. `GET /users/me/files/{nome_do_arquivo}`

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

Em producao, as contas autenticadas ficam persistidas no MongoDB, colecao `users`.

No fallback local, as contas e metadados ficam em SQLite.

Esses registros armazenam:

- email normalizado
- `user_id`
- `display_name`
- hash de senha com PBKDF2 SHA-256
- datas de criacao e atualizacao
- `plan`, `subscription_status`, `stripe_customer_id` e `stripe_subscription_id` (billing)
- cota mensal de `POST /processar` em `processar_usage` (`user_id` + `YYYY-MM` UTC)
- `deleted_at` (soft-delete LGPD; contas apagadas saem de `get_user_by_*`)
- `consent_log` append-only, sem `ON DELETE CASCADE` — sobrevive ao purge

### Dados do usuario

Em producao, os dados de negocio ficam em colecoes MongoDB separadas por `user_id`:

- `user_profiles`
- `user_profile_versions`
- `user_documents`
- `embedding_runs`
- `embedding_chunks`
- `processing_runs`
- `job_analysis_insights`
- `development_plans`
- `generated_files`
- `processar_usage`
- `stripe_webhook_events`

Arquivos gerados para download continuam em:

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
- `MONGODB_URI`
- `MONGODB_DATABASE`
- `PERSISTENCE_BACKEND`
- `STRIPE_SECRET_KEY` (pode faltar em teste/local; checkout fica `503`)
- `STRIPE_PRICE_ESSENCIAL` (Price ID mensal do Essencial R$19,90)
- `STRIPE_WEBHOOK_SECRET` (pode faltar em teste/local; webhook fica `503`)
- `STRIPE_CHECKOUT_SUCCESS_URL`
- `STRIPE_CHECKOUT_CANCEL_URL`
