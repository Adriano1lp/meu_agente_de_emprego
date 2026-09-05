# P0 audit — paid beta of Meu Agente de Emprego

**Scope:** investigation only. No product feature code was changed.

**Repos reviewed**

| Repo | Access | Result |
|---|---|---|
| API `Adriano1lp/meu_agente_de_emprego` (this repo, `master` @ `e6c7f5b`) | Full | Source of all API evidence below |
| Flutter `https://github.com/Adriano1lp/meuagentedeemprego-app` | **Not accessible** | `git clone` → `Repository not found`; `gh repo view` → GraphQL cannot resolve the name. Not in `gh repo list Adriano1lp`. Cloud environment repos = API only. |

**Status key:** `DONE` / `PARTIAL` / `MISSING` / `UNKNOWN`

---

## Executive snapshot

| # | Item | Status | Paid-beta blocker? |
|---|---|---|---|
| 1 | Terms + privacy checkbox on signup + versioning / consent log | **PARTIAL** | Yes — legal |
| 2 | LGPD: account deletion + data export | **PARTIAL** (delete scaffold only) / **MISSING** (export) | Yes — legal |
| 3 | Billing + quotas/credits | **MISSING** | Yes — cannot charge |
| 4 | Security: JWT storage (not plain Hive) + HTTPS / no cleartext | **UNKNOWN** (app) / **PARTIAL** (API) | Yes — if app stores JWT in Hive |
| 5a | Password reset **email** | **PARTIAL** | Yes — lockout risk |
| 5b | PDF / object storage | **PARTIAL** | Yes — paid artifacts die on Render restart |
| 5c | Job search simulated vs real | **MISSING** (no search) | No, if product is scoped as “paste job text” |

The API is a working JWT + FastAPI job-analysis backend for a small homol/prod (docs say ~5 users). It is **not** a paid-beta stack: no billing, no LGPD user rights, no consent versioning, no email, no durable PDF store.

---

## Stack overview

| Layer | What exists |
|---|---|
| API | FastAPI 0.138 + Uvicorn, custom HS256 JWT (`auth.py`), PBKDF2-SHA256 passwords (`services/auth_users.py`) |
| LLM | LangChain + OpenAI (`gpt-4o`, `text-embedding-3-small`) in `services/main_chat.py` / `services/main_rag.py` |
| Persistence | MongoDB in production (`database/mongo_repository.py`); SQLite fallback (`database/schema.sql`, `PERSISTENCE_BACKEND=sqlite`) |
| Files | Local disk under `storage/` — CVs, Chroma embeddings (SQLite mode), generated PDFs |
| PDF | ReportLab (`services/main_curriculo.py`, `services/main_carta.py`) |
| Deploy target | Render Web Service, documented in `DEPLOY.md` |
| Client | Flutter app referenced in `DEPLOY.md` line 225; source not in this environment |

There is no README. There are no automated tests. There is no rate limiter, plan table, payment provider, SMTP/email client, or object-storage SDK in `requirements.txt`.

---

## How to run locally

From `DEPLOY.md` and `.env.example`:

```bash
cp .env.example .env
# set OPENAI_API_KEY; leave PERSISTENCE_BACKEND=sqlite for local
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- Base URL: `http://localhost:8000` (`documento.md`)
- Health: `GET /health`
- Optional SQLite bootstrap: `python scripts/create_sqlite_db.py` (schema also auto-applies on first DB use)
- Docker:

```bash
docker build -t analista-vagas-api .
docker run --env-file .env -p 8000:8000 analista-vagas-api
```

`services/main_chat.py` calls `ensure_openai_api_key()` at import time, so the process will not start without `OPENAI_API_KEY`.

Local CORS default is `*` (`.env.example`). Production startup (`config.ensure_runtime_config`) refuses `*`, default `JWT_SECRET`, and missing MongoDB.

---

## How it deploys (documented)

Documented in `DEPLOY.md` only. Target is **Render**:

1. Web Service, `pip install -r requirements.txt` **or** the `Dockerfile` (`uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers`).
2. Start without `--reload`.
3. Required env: `OPENAI_API_KEY`, `ENVIRONMENT=production`, `AUTH_MODE=jwt`, `JWT_SECRET`, `CORS_ALLOW_ORIGINS` (real app origin, not `*`), `MONGODB_URI`, `MONGODB_DATABASE`.
4. Recommended: `PUBLIC_BASE_URL`, `STORAGE_DIR`, `JWT_EXPIRATION_MINUTES` (default 10080 = 7 days), `MAX_UPLOAD_SIZE_MB`, model names.
5. MongoDB holds accounts, profiles, CV text/metadata, embeddings, processing runs. **PDFs stay on the Render filesystem** and are lost on restart unless a disk or external store is added.
6. Smoke list: `/health`, register, login, `/auth/me`, upload-cv, rebuild-embeddings, status, `/processar`, file download.

No CI/CD workflow, Render `render.yaml`, or IaC lives in this repo.

---

## 1. Terms + privacy checkbox on signup + versioning / consent log

**Status: PARTIAL**

### What is DONE (API)

- `POST /auth/register` requires `terms_accepted=true` or returns 400.
  - Model: `main.py` `AuthRegisterRequest.terms_accepted`
  - Guard: `services/auth_users.py` `register_user()`
- Persistence of a boolean + timestamp:
  - SQLite: `database/schema.sql` `users.terms_accepted`, `users.terms_accepted_at`
  - Mongo: `database/mongo_repository.py` `create_user` / `accept_user_terms`
- Existing accounts can accept later: `POST /users/me/terms/accept` (`main.py`).
- Protected product routes use `_require_terms_accepted` → 403 `"Aceite do termo de uso obrigatorio"`.
- `GET /users/me` returns `terms_accepted` + `terms_accepted_at` so a client can prompt legacy users (`documento.md`).

### What is MISSING

| Gap | Evidence |
|---|---|
| No privacy-policy field, only “termo de uso” | Grep: no `privacy`, `privacidade`, `lgpd`, `consent` |
| No document version (`terms_version`, `privacy_version`) | User row is a single bool + timestamp; accept overwrites |
| No append-only consent log (IP, user-agent, document hash/URL, version) | No `consent_events` table/collection; `accept_user_terms` is an `UPDATE` |
| No hosted terms/privacy text or URL in the API | No static legal docs in repo |
| Flutter signup checkbox | **UNKNOWN** — app repo inaccessible |

### Implication

Server-side “I accepted something” exists. You cannot prove *which* terms/privacy text, *which* version, or that privacy was accepted separately. That is not enough for a paid Brazilian launch.

---

## 2. LGPD: account deletion + data export

**Status: PARTIAL (deletion column only) + MISSING (export and purge)**

### Deletion — PARTIAL

- Schema anticipates soft delete: `users.deleted_at` (`database/schema.sql`).
- Reads filter `deleted_at IS NULL` (SQLite `database/repository.py`; Mongo `database/mongo_repository.py`).
- There is **no** `DELETE /users/me` (or any delete route). `main.py` routes are health, auth, terms, CV/profile, embeddings, files, gap-history, PDI, `/processar`, cover-letter.
- There is **no** `delete_user` / `soft_delete` function. `deleted_at` is never written after insert (`None`).
- Cascade `ON DELETE CASCADE` on child tables would only help a hard SQL delete that nobody calls.
- Local files would remain: `storage/users/{user_id}/documents/`, `chroma/`, `outputs/` (`config.py`, `DEPLOY.md`). Mongo collections (`user_profiles`, `user_documents`, `embedding_chunks`, `processing_runs`, `job_analysis_insights`, `development_plans`, `generated_files`, `password_reset_tokens`) have no user-purge helper.

### Export — MISSING

- No `/users/me/export`, portability bundle, or admin dump.
- Profile `GET /users/me/profile` and `GET /users/me/gap-history` are partial reads, not an Art. 18 portability package (account + CV + embeddings metadata + PDFs + processing history).

### Implication

LGPD rights of exclusion and portability are not implementable from the product. Paid beta that stores CVs, emails, and LLM job text without these is a legal blocker.

---

## 3. Billing + quotas/credits (plans, checkout, webhooks, server-side limits)

**Status: MISSING**

| Piece | Evidence |
|---|---|
| Plans / catalog | No plan, price, or entitlement model in schema or Mongo indexes |
| Checkout | No Stripe / Mercado Pago / Asaas / Iugu client; not in `requirements.txt` |
| Webhooks | No `/webhooks/*` route in `main.py` |
| Credits / quotas | No credit balance, daily cap, or entitlement check on `/processar` or cover-letter |
| Server-side limits that exist | Upload size `MAX_UPLOAD_SIZE_MB` (`services/user_data.py`); PDI `limit` 1–20; gap-history page size; **match_score &lt; 60** skips PDF (`services/main_chat.py` `MINIMUM_MATCH_SCORE_TO_GENERATE_CURRICULUM`) — cost-saving, not billing |

Any authenticated user who accepted terms can call `/processar` and `/users/me/cover-letter` unbounded. That is an OpenAI-cost and abuse hole for a paid beta.

---

## 4. Security: JWT storage (not plain Hive), HTTPS / no cleartext in mobile configs

**Status: UNKNOWN (mobile) / PARTIAL (API)**

### API — PARTIAL

| Control | Status | Evidence |
|---|---|---|
| JWT issued + verified (HS256 HMAC) | Done | `auth.py` `create_access_token` / `decode_access_token` |
| Production refuses default secret | Done | `config.py` `ensure_runtime_config` |
| Production refuses CORS `*` | Done | same |
| Production requires Mongo | Done | same |
| HTTPS enforced in app | Missing | Relies on Render TLS; `PUBLIC_BASE_URL` examples are `https://…` in `DEPLOY.md`; local docs are `http://localhost:8000` |
| Legacy `X-User-Id` | Residual risk | Allowed when `AUTH_MODE != jwt` (`auth.py` `get_current_user_id`) |
| JWT lifetime | Long | Default `JWT_EXPIRATION_MINUTES=10080` (7 days); no refresh/revoke list |
| Password hashing | Done | PBKDF2-SHA256, 200k iterations (`services/auth_users.py`) |

### Flutter — UNKNOWN

Cannot inspect Hive vs `flutter_secure_storage`, Android `usesCleartextTraffic`, iOS ATS, or baked `http://` API URLs.

`DEPLOY.md` only states: *“O app Flutter atual ja consome a API com JWT”*. Treat JWT-in-Hive and cleartext API base URL as **unverified launch risks** until the app repo is granted to this environment.

---

## 5. Bonus items

### 5a. Password reset email — PARTIAL

**Done**

- `POST /auth/password-reset/request` and `POST /auth/password-reset/confirm` (`main.py`)
- Token hashed (SHA-256), expiry (`PASSWORD_RESET_EXPIRATION_MINUTES`, default 30), single-use (`used_at`)
- Generic response text to avoid email enumeration (`PASSWORD_RESET_GENERIC_MESSAGE`)

**Missing (the email)**

- `request_password_reset` creates a row and, unless production, **returns `reset_token` in JSON**. It never sends mail.
- No SMTP / SendGrid / SES / Resend / Mailgun dependency or env var in `.env.example`.
- In production the user sees “Se o email estiver cadastrado, enviaremos instrucoes…” and receives nothing.

So the recovery *protocol* exists; the *channel* does not. Paid-beta users who forget passwords are locked out.

### 5b. PDF / object storage — PARTIAL

**Done:** generate + serve PDFs from disk.

- Write: `get_user_output_dir(user_id) / {uuid}.pdf` in `main.py` `/processar` and `/users/me/cover-letter`
- Read: `GET /users/me/files/{file_name}` (path-traversal guarded; user-scoped)
- Metadata: `generated_files` table/collection

**Missing:** S3/R2/GCS or any object-store client. `DEPLOY.md` is explicit that Render disk is ephemeral and PDFs will vanish across redeploy/restart. Paying users for a CV that disappears is a product-breaking defect.

CV originals also live on local disk (`storage/users/{user_id}/documents/`) even when text is copied into Mongo.

### 5c. Job search simulated vs real — MISSING (as search)

There is **no** job-board integration, crawler, or search index.

`POST /processar` accepts `{ "texto": "<user-pasted job description>" }` and runs a real LLM pipeline (parse job → match CV embeddings → optional optimized CV). That is **analysis of pasted text**, not search.

If the Flutter UI shows a “buscar vagas” feed, it is not backed by this API. Treat any in-app list as **UNKNOWN / likely simulated** until the app is readable.

Honest beta copy: “Cole a descricao da vaga.” Shipping a fake job marketplace is a trust risk, not a legal P0.

---

## Flutter cross-check (access failure)

Attempted 2026-09-05:

```text
git clone https://github.com/Adriano1lp/meuagentedeemprego-app
→ remote: Repository not found

gh repo view Adriano1lp/meuagentedeemprego-app
→ Could not resolve to a Repository

gh repo list Adriano1lp
→ public repos only; app not listed
```

Also tried name variants (`meu_agente_de_emprego_app`, `meu-agente-de-emprego-app`, `meuagentedeemprego`). None resolve.

**Needed from the app repo (when access is granted):**

1. Signup screen: terms **and** privacy checkboxes; whether they send `terms_accepted: true`.
2. Token persistence: Hive box vs `flutter_secure_storage` / Keychain / Keystore.
3. API base URL: `https://` only; Android `network_security_config` / `usesCleartextTraffic`; iOS ATS exceptions.
4. Account delete / export UI.
5. Paywall / plan screens talking to missing API.
6. Password-reset UI (token-in-response vs email deep link).
7. Job list: mock fixtures vs HTTP.

Until then, items 1 (UI), 4 (mobile security), and 5c (job UI) stay UNKNOWN on the client side.

---

## Ordered week-2 implementation backlog

Priority is legal → chargeability → security → recovery → durability. Effort is described by surface area, not calendar days.

### P0-1 — Versioned terms + privacy + consent log

**Why first:** every other beta user will be created under the wrong legal record if you wait.

- Publish Terms and Privacy (URLs + `terms_version` / `privacy_version` constants).
- Split register flags: `terms_accepted` + `privacy_accepted` (both required).
- Append-only `consent_events`: user_id, document, version, accepted_at, ip, user_agent, source (`register` / `reaccept`).
- Re-prompt when version bumps (`POST /users/me/terms/accept` should take versions, not a bare `accepted: true`).
- Flutter: two checkboxes + links; block register until both true.
- Keep `_require_terms_accepted` but key it off current versions.

**Touches:** `main.py`, `services/auth_users.py`, `database/schema.sql`, `database/mongo_repository.py`, `documento.md`, Flutter signup.

### P0-2 — LGPD delete + export

**Why:** Art. 18 rights; `deleted_at` is already on `users`.

- `GET /users/me/export` — JSON (or zip) of user, profile versions, documents/text, insights, PDIs, generated-file metadata; signed short-lived download for PDFs still on disk.
- `DELETE /users/me` — confirm password; set `deleted_at`; purge or anonymize Mongo/SQLite children; delete `storage/users/{user_id}`; revoke reset tokens; reject subsequent JWTs.
- Flutter: settings actions + confirmation.

**Touches:** new service + routes; both repositories; `config.get_user_*` dirs; Flutter settings.

### P0-3 — Billing, plans, webhooks, server-side quotas

**Why:** there is no paid beta without this. Unbounded `/processar` will burn the OpenAI key.

- Plan catalog (free/beta vs paid): e.g. N analyses / cover letters per period.
- Checkout (Mercado Pago or Stripe) + `POST /webhooks/...` signature verification.
- Persist `subscriptions` / `credit_ledger`; check **before** LLM calls in `/processar` and `/users/me/cover-letter`.
- Do not trust the client for remaining credits.
- Flutter: paywall + restore.

**Touches:** new tables, webhook route, `main.py` processar/cover-letter, Flutter paywall. Largest new subsystem.

### P0-4 — Mobile secret storage + HTTPS-only

**Why:** 7-day JWT in a Hive box is session theft; HTTP base URL is credential theft.

- App: `flutter_secure_storage` (or equivalent); wipe Hive token keys.
- App: HTTPS-only API; disable cleartext in Android/iOS configs.
- API (optional harden): drop non-jwt `X-User-Id` in production; shorten access TTL + refresh; consider token revoke on password change.

**Touches:** Flutter storage + flavors; small `auth.py` / `config.py` harden. **Blocked on app repo access.**

### P0-5 — Password reset actually sends email

**Why:** protocol is already in `services/auth_users.py`; production currently lies.

- Add a mail provider; send a link (not the raw token in JSON).
- Keep `PASSWORD_RESET_EXPOSE_TOKEN` **off** in production (already the default).
- Flutter: forgot-password screen + deep link to confirm.

**Touches:** `services/auth_users.py`, env, Flutter auth.

### P0-6 — Durable PDF (and preferably CV) object storage

**Why:** `DEPLOY.md` already flags ephemeral Render disk.

- Store generated PDFs (and CV originals) in S3-compatible storage; keep `generated_files.public_url` as API-gated signed URLs.
- `GET /users/me/files/{name}` streams from the bucket.
- Fail `/processar` if upload to the bucket fails (do not report a URL that will 404 after restart).

**Touches:** `main.py` file helpers, `config.py`, Render env, `DEPLOY.md`.

### P0-7 — Job search product decision (not a legal P0)

- Keep paste-to-`/processar` and say so in the UI, **or**
- Add a real search (ATS/board API) — new product surface, not a patch.

Do not invent a simulated feed for paid users.

---

## Suggested week-2 sequence

```text
Day-order (technical, not calendar estimates):

  1. Consent versioning + Flutter checkboxes     (P0-1)
  2. DELETE + EXPORT                             (P0-2)
  3. Mail provider + wire reset                  (P0-5)  — small, unblocks QA accounts
  4. Object storage for PDFs                     (P0-6)
  5. Plans + webhook + quota on /processar       (P0-3)
  6. Secure token store + HTTPS audit in app     (P0-4)  — needs app repo
  7. Copy/scope for job “search”                 (P0-7)
```

P0-3 is the most invasive. P0-1 and P0-2 should land before inviting paying testers. P0-4 cannot be closed from this API repo alone.

---

## Out of scope notes (not P0, but adjacent)

- Custom JWT (no `PyJWT`) works but has no `nbf`/kid/rotation.
- No automated test suite for auth, consent, or billing.
- `AUTH_USERS_FILE` / `users.json` still mentioned in `DEPLOY.md` stack blurb; live path is Mongo/SQLite.
- `checklist.md` is gitignored — may contain private task IDs (`Task-026` = password reset, `Task-022` = terms) but is not in git.
- Production readiness checks in `config.py` are good; they do not substitute billing or LGPD.

---

## Evidence index (API paths)

| Topic | Paths |
|---|---|
| Routes | `main.py` |
| JWT | `auth.py`, `config.py` |
| Register / terms / reset | `services/auth_users.py` |
| Schema | `database/schema.sql`, `database/repository.py`, `database/mongo_repository.py` |
| Upload limits | `services/user_data.py` |
| Job analysis / score gate | `services/main_chat.py` |
| RAG / local chroma | `services/main_rag.py` |
| Env | `.env.example` |
| Run / deploy | `DEPLOY.md`, `Dockerfile`, `documento.md` |
| Deps | `requirements.txt` |
