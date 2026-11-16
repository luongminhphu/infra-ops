# Architecture – infra-ops

infra-ops is an internal platform for IT Infrastructure operations. It is built as a
**Platform Core** that hosts independent **Modules**.

```
┌──────────────────────────── frontend (React) ────────────────────────────┐
│  core: shell, design system, auth, pages (dashboard, connections, …)    │
│  modules/<id>: routes + pages        nav comes from backend manifests    │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │ /api (same origin, session cookie + CSRF header)
┌──────────────────────────── backend (FastAPI) ───────────────────────────┐
│ app/core                                                                 │
│   auth (local / Entra OIDC / dev) · rbac · audit · secrets · connections │
│   connectors (registry + SDP, BioStar …) · dashboard · modules registry  │
│ app/modules/<id>                                                         │
│   manifest.py → permissions, routers, nav, widgets, connectors_used      │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                            PostgreSQL 16  (one schema, tables prefixed core_ / <module>_)
```

## Core responsibilities

| Area | Package | Notes |
|---|---|---|
| Configuration | `core/config.py` | Infrastructure only (DB, master keys, SSO). External-system credentials are **never** in env. |
| Auth | `core/auth` | Server-side sessions (SHA-256 of token stored), httpOnly cookie, CSRF header on mutations. Modes: `local` (scrypt, lockout), `oidc` (Entra, code + PKCE), `dev` (blocked in production). |
| RBAC | `core/rbac` | Permission codes `<module>.<resource>.<action>` declared by manifests. System roles Admin (all permissions, locked), Helpdesk, Viewer. Default grants applied once, the first time a permission appears. |
| Audit | `core/audit` | Append-only table (DB trigger rejects UPDATE/DELETE/TRUNCATE) + SHA-256 hash chain, verifiable from the UI. Values are redacted before storage. |
| Secrets | `core/secrets` | AES-256-GCM, versioned keyring (`INFRAOPS_MASTER_KEYS`), AAD binds ciphertext to `credential:<profile>:<purpose>`. |
| Connections | `core/connections` | Connection Profiles (non-secret config, validated by the connector schema) + Credentials per purpose (`read` / `write`). `open_connection()` is the only way modules get credentials. |
| Connectors | `core/connectors` | Plugin registry. Each connector declares config schema, auth types and `test_connection()` (DNS → TLS → auth → real permission probes). Shared HTTP client: timeouts, retry with backoff + jitter, `Retry-After`. |
| Dashboard | `core/dashboard` | Widgets declared by manifests, filtered by permission; a failing widget does not break the page. |
| Logging | `core/logging.py` | structlog JSON, correlation ID (`X-Request-ID`), secret redaction. |

Planned core services (later phases): workflow engine (Job/Step, Postgres-backed queue with
`FOR UPDATE SKIP LOCKED`), scheduler, document templates (docxtpl + LibreOffice), file storage,
notifications.

## Module boundaries

- A module lives in `backend/app/modules/<id>/` and `frontend/src/modules/<id>/`.
- It imports only `app.core.*`. `import-linter` (CI) enforces: modules never import each other,
  core never imports modules.
- Tables are prefixed `<id>_` and migrations live in the module's own Alembic branch.
- Modules are enabled with `INFRAOPS_ENABLED_MODULES`; a disabled module keeps its data but
  exposes no routes, nav, permissions or widgets.

See [adding-a-module.md](adding-a-module.md).

## Security model

- Browser never sees tokens of external systems; secrets are write-only through the API and
  displayed as `••••abcd`.
- Read and Write credentials are separate; without Write, write operations raise
  `CapabilityUnavailable` with a user-facing reason.
- Every configuration change and login is audited (who, when, module, system, before/after, result).
- Production guards: dev auth refused, master key and signing secret required.
- Web container sends CSP, `X-Frame-Options: DENY`, `nosniff`.

## Deployment

- `docker-compose.yml` for a single host; `deploy/helm/infra-ops` for Kubernetes (Rancher):
  API + web Deployments, a pre-install/upgrade migration Job, Ingress, optional internal CA bundle.
- Health: `/health/live` (process) and `/health/ready` (database).

## Integration status

| System | Status |
|---|---|
| ServiceDesk Plus (on-prem authtoken / cloud Zoho OAuth) | Connector + test (Phase 1). Data adapters Phase 2. |
| Suprema BioStar 2 | Connector + test with session handling (Phase 1). Data adapters Phase 2. |
| Microsoft Entra ID, Active Directory, Exchange hybrid | Deferred by decision (see phase-0-proposal §11–12). Registry ready. |
