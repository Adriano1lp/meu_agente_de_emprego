# MAE — Regras de negócio (canônico)

**Produto:** Meu Agente de Emprego (MAE)  
**Fonte da verdade PO:** este documento  
**Vigência:** 30/09/2026 (America/Sao_Paulo)  
**Idioma:** pt-BR

Documentos BDD/QA mais antigos (ex.: soft-launch de set/2026) podem estar defasados; em conflito, **prevalece este arquivo**.

---

## 1. Visão e escopo

- MAE ajuda o candidato a analisar vagas, gerar CV otimizado (PDF) e carta de apresentação, com base no currículo (embeddings).
- Superfícies atuais: **app Flutter**, **web** e **API**.
- Momento: **soft launch** / fatias diárias controladas.
- **Fora de escopo atual (não fatia do dia):** Stripe Checkout UI / fatia W3 e trabalho de billing no cliente, **salvo ordem explícita de Money ou Adriano**.
- UI completa de billing e (parcialmente) export/delete no app também ficam pós-beta, salvo decisão Money/PO.

---

## 2. Estados de fatia (GO / FECHADA)

Cadeia de custódia obrigatória:

1. BDD / critério de aceite da fatia  
2. PR aberto (1 PR por vez)  
3. **Seg 🟢**  
4. **QA ACEITO**  
5. **PO GO** (formal)  
6. Adriano faz **merge**  
7. Infra: deploy API (Render) / App Distribution / Static web  
8. Smoke live e/ou **device** (quando aplicável)  
9. **PO FECHADA**

Regras operacionais:

- **1 fatia por dia**, **1 PR por vez**, sem trabalho paralelo de fatias.
- **PO GO** só depois de Seg 🟢 **e** QA ACEITO.
- UX mobile: **não marcar FECHADA** sem confirmação em device quando a fatia tocar interface/comportamento no aparelho.
- Merge sem GO formal do PO não fecha a cadeia.

---

## 3. Auth / consentimento / LGPD

### Cadastro e termos

- Signup (`POST /auth/register`) exige aceite das versões vigentes `CURRENT_TERMS` / `CURRENT_PRIVACY` (campos de aceite + versão).
- Versão inventada pelo client → rejeição; **não** cria usuário nem log.
- Cada aceite grava registro **append-only** em `consent_log` (sem update/delete).
- Reaceite autenticado: `POST /consent` com documento + versão vigente → novo append no log.

### Gate de consentimento

- Em rotas sensíveis (ex.: status, processar, upload, carta, export), se `terms_version` ou `privacy_version` ≠ vigente → **403** com `TERMS_OUTDATED` ou `PRIVACY_OUTDATED`.
- Exceções típicas do gate: `POST /consent` e `GET /legal/*`.

### Sessão

- Auth recomendada: JWT Bearer.
- App: token só em cofre (`flutter_secure_storage`); release HTTPS-only.
- Web: JWT em memória (paridade com política de não persistir token em storage inseguro).

### Direitos do titular

- **Export** autenticado dos dados relevantes (sem senha, hash, JWT ou secrets).
- **DELETE** da conta exige confirmação explícita (`confirm: "DELETE"`).
- Purge: PII scrub/anonimização, embeddings, arquivos/objetos sob `users/{user_id}/`, processamentos ligados ao usuário.
- **`consent_log` permanece** (append-only, sem cascade).

```gherkin
Scenario: Delete confirma e preserva consent_log
  Given autenticado
  When confirmo DELETE da conta
  Then PII e objetos do usuário são purged
  And consent_log permanece intacto
  And não consigo mais autenticar com as credenciais antigas
```

---

## 4. CV, embeddings e status

- Upload de CV (`POST /users/me/upload-cv`) persiste o arquivo e, nas regras vigentes (B-Cota), **tenta construir embeddings** no mesmo fluxo.
- Sucesso de embeddings → cliente/API expõem prontidão via `GET /users/me/status`:
  - `has_cv`, `has_embeddings`, `ready_for_analysis` (quando retornado no upload)
  - também cota/plano quando a fatia de status já estiver live (`plan`, `used`, `limit`, `remaining`, …)
- Sem embeddings:
  - **Análise** (`/processar`) e **Carta** ficam **bloqueadas no client**.
  - Backend já rejeita `/processar` sem embeddings (**antes** de debitar cota).
  - Backend deve rejeitar **cover-letter** sem embeddings (débito de backlog se o gate server-side ainda for incompleto; app #9 já gateia por `has_embeddings`).
- `POST /users/me/rebuild-embeddings` permanece disponível para recuperação / idempotência após falha de embedding no upload.
- Formatos de CV aceitos: `.txt` e `.pdf` (limites de tamanho da API).

---

## 5. Cota / processar (B-Cota — PR API #15 **FECHADA** — regras vigentes)

### Planos e contador

| Plano | Cota de `POST /processar` | Observação |
| --- | --- | --- |
| Free | **5** / mês civil **UTC** | Sem assinatura `active` |
| Essencial | **30** / mês civil **UTC** | `subscription_status=active` |
| `past_due` | trata como Free (5) | grace 0 |

- Contador por `user_id` + `YYYY-MM` (UTC); zera no 1º dia UTC do mês.
- Nesta fatia, a cota vale para **`/processar`**. Carta / PDI / embeddings **não** debitam essa cota (carta: Money ainda avalia se permanece assim).

### Ordem no `/processar`

1. Auth + consentimento vigentes.  
2. Validar `texto` (**1–20.000** caracteres).  
3. Checar **embeddings** → se ausentes: **400**, **sem debitar**.  
4. Reservar / debitar cota.  
5. Pipeline de análise / PDF.

### Débito e estorno (refund)

- **Estorno só** em falhas de **provider/infra** (timeout, conexão, rate limit, 5xx do provedor, falha de storage/DB), com **cap de 3 refunds/mês** por usuário no período.
- Documentos Mongo antigos **sem** campo `refunds`: tratados com `$or` / `$exists: false` (contam como elegíveis ao teto).
- Erros de **parse / JSON inválido do LLM** (lado usuário/modelo malformado) → **422** com mensagem fixa e **ainda debitam** (não estornam).
- Após PDF já persistido, falha posterior **não** devolve cota.
- Texto vazio após `strip` → **400**, sem débito. Fora do range 1–20k → validação **422**, sem pipeline e sem débito.

### Códigos de negócio (cota)

- **402** com `QUOTA_EXCEEDED` ou `SUBSCRIPTION_REQUIRED` (Free esgotado tipicamente `SUBSCRIPTION_REQUIRED`; Essencial esgotado `QUOTA_EXCEEDED`), com `used` / `limit` / `plan` no detalhe — sem secrets Stripe no body.
- Match baixo (`match_score` &lt; 60): resposta 200 com análise, **sem** PDF (`generation_blocked`); a chamada **consome** cota se chegou ao pipeline com reserva válida.

```gherkin
Scenario: Sem embeddings não debita
  Given autenticado com termos ok e cota disponível
  And has_embeddings = false
  When POST /processar com texto válido
  Then 400
  And used da cota permanece igual
```

---

## 6. Carta (cover letter)

### Estado das superfícies

- **Web W-Carta #8:** merged / live no Static. Confirmação device Adriano pode ainda estar pendente para FECHADA formal se aplicável.
- **App #9:** gate `has_embeddings` + exibição de `detail` em **402/422**; Apple device com GO de aparelho; **FECHADA** pendente ajuste de data (abaixo).

### Contrato de produto

- `POST /users/me/cover-letter` com empresa (ex.: a partir do histórico de análises).
- Texto da carta em memória no client; PDF via download autenticado do arquivo do dono (`GET /users/me/files/{nome}`).
- Sem embeddings: client bloqueia; backend deve falhar com mensagem clara (débito se gate incompleto).
- **Carta pode não consumir** a cota de `/processar` (regra Money em avaliação — não assumir débito até decisão).

### Ajuste em curso (30/09/2026)

- Prompt da API ainda contém o literal **`[data atual]`**.
- **Backend** corrige para a **data real de geração (D)** formatada em **pt-BR**.
- **Mobile** em standby até o backend publicar a correção; depois smoke device e só então FECHADA da fatia de carta/app conforme cadeia.

---

## 7. Web vs App

- Paridade das **jornadas principais**: signup/consent, upload CV / prontidão, processar + cota, histórico, carta, erros 401/402/403/422.
- **Web:** Static no Render (repo web).
- **App:** distribuição via **Firebase App Distribution** (builds de release apontando HTTPS para a API).
- Diferenças aceitas no soft launch: UI de billing / export-delete mais madura na web ou só API, conforme fatias liberadas.

---

## 8.Repos

| Camada | Repositório | Branch / deploy |
| --- | --- | --- |
| API | `Adriano1lp/meu_agente_de_emprego` | `master` → Render auto-deploy |
| Web | `Adriano1lp/meuagentedeemprego-web` | Static Render |
| App | `Adriano1lp/meuagentedeemprego-app` | Firebase App Distribution (linha de release vigente) |

Merge na branch de deploy **somente** após PO GO na cadeia da fatia.

---

## 9. Backlog conhecido (não fatia do dia)

Lista curta — não puxar para o dia sem ordem Money/PO:

- B7: empty-context **400** deveria **refund** (avaliar alinhamento com política de estorno).
- Rate-limit em **cover-letter**.
- Mapear cópias fixas de UI para **402/422** (web/app).
- Gate server-side duro de cover-letter sem embeddings (se ainda houver débito).
- Stripe/W3 UI e portal — só com ordem explícita Money/Adriano.
- Rate limit / checksum no upload; demais débitos de segurança pós-launch.

---

## 10. Fontes

Este documento é a **fonte da verdade do PO** em **30/09/2026**.

Síntese a partir de (preferindo o mais novo / vigente):

- Memória operacional PO / cadeia GO–FECHADA e fatias Carta / B-Cota  
- `mae-qa/bdd-w2-processar-cotas.md`  
- `mae-qa/bdd-soft-launch-dod.md` (estrutura; pode **atrasar** vs. regras atuais — ex. refunds, upload→embeddings, carta)  
- `mae-api/documento.md` (significado de negócio dos endpoints; não é catálogo para colar)  
- PRs fechados relevantes: API **#15** (B-Cota), Web **#8** (W-Carta), App **#9** (gate carta), status cota (#14)  
- Skim: `mae-qa/ENTREGA-1-inventario-e-dod.md` (histórico de gaps; não redefine regra viva)

**Sem segredos** neste arquivo. APIs não inventadas: apenas contratos já descritos nas fontes acima.
