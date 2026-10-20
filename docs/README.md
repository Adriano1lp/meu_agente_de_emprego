# MAE — Product (PO)

Documentação canônica de produto do **Meu Agente de Emprego**.

## Documento principal

→ [`regras-negocio-mae.md`](regras-negocio-mae.md) — regras de negócio vigentes (pt-BR), fonte da verdade do PO a partir de **30/09/2026**.

Em conflito com BDDs/QA mais antigos (ex. soft-launch), prevalece esse arquivo.

## Escopo

Estes documentos vivem em `docs/` neste repositório da API (`Adriano1lp/meu_agente_de_emprego`).

## Sessão web (API)

O app segue com Bearer e `JWT_EXPIRATION_MINUTES`. A web, em opt-in (`session: "cookie"` ou `X-MAE-Client: web`), recebe access curto e refresh opaco no cookie `__Secure-mae_rt` (`HttpOnly`, `Secure`, `SameSite=None`, `Partitioned`, `Path=/auth`, sem `Domain`). Rotação, logout, CSRF (`Origin` na allowlist e `X-MAE-CSRF: 1`) e revogação estão em [`documento.md`](../documento.md), na seção "Sessao persistente da web".

Variáveis: `WEB_ACCESS_TOKEN_MINUTES`, `REFRESH_TOKEN_IDLE_DAYS`, `REFRESH_FAMILY_MAX_DAYS`, `JWT_ACCEPT_LEGACY_UNTYPED`, `JWT_LEGACY_UNTYPED_CUTOFF_ISO`, `RATE_LIMIT_TRUST_PROXY_HOPS`, `REFRESH_RATE_LIMIT_PER_IP_ENABLED`.

No deploy, `JWT_LEGACY_UNTYPED_CUTOFF_ISO` recebe o horário do deploy. O limite de `POST /auth/refresh` vale por processo.

O limite de gravação do CV estruturado (`POST` e `PUT /users/me/cv/structured`, `STRUCTURED_CV_RATE_LIMIT_PER_HOUR`) fica em memória, vale por processo e assume um único worker. Antes de escalar, esse contador precisa migrar para o banco ou para o Redis.
