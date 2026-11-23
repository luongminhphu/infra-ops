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
| RBAC | `core/rbac` | Permission codes `<module>.<resource>.<action>` declared by manifests. System roles Admin (all permissions, locked), Manager, Operator, Viewer; a first SSO login gets Viewer. Default grants applied once, the first time a permission appears. |
| Audit | `core/audit` | Append-only table (DB trigger rejects UPDATE/DELETE/TRUNCATE) + SHA-256 hash chain, verifiable from the UI. Values are redacted before storage. |
| Secrets | `core/secrets` | AES-256-GCM, versioned keyring (`INFRAOPS_MASTER_KEYS`), AAD binds ciphertext to `credential:<profile>:<purpose>`. |
| Connections | `core/connections` | Connection Profiles (non-secret config, validated by the connector schema) + Credentials per purpose (`read` / `write`). `open_connection()` is the only way modules get credentials. |
| Connectors | `core/connectors` | Plugin registry. Each connector declares config schema, auth types and `test_connection()` (DNS → TLS → auth → real permission probes). Shared HTTP client: timeouts, retry with backoff + jitter, `Retry-After`. |
| Dashboard | `core/dashboard` | Widgets declared by manifests, filtered by permission; a failing widget does not break the page. Modules provide the renderer for their widget `kind` in the frontend. |
| Module settings | `core/settings` | Typed (pydantic) per-module configuration in `core_module_setting`; invalid stored values fall back to defaults; changes audited as diffs. |
| Files | `core/storage` | `core_file` metadata + `LocalFileStorage` (directory / PVC; S3 later). Uploads are type-checked by magic bytes and size-limited. |
| Documents | `core/documents` | `DocumentKind`s declared by modules. Versioned DOCX templates (bundled default seeded as v1; uploads validated, unknown placeholders flagged), rendering with docxtpl (autoescape), PDF via LibreOffice with an isolated profile per conversion, monthly numbering `PREFIX-YYYYMM-####` (atomic upsert), lifecycle draft → finalized → signed / void. |
| Workflow | `core/workflow` | Generic Job/Step engine. Workflows declared in manifests; the worker (`core.workflow` task, every 15 s or on demand) advances jobs under row locks: automatic steps, manual steps (prepare + validate input), typed confirmation, waits with timers/timeouts, failure + retry, skip, cancel, admin override-close, dry-run preview. Job log (`core_job_event`) + audit with before/after state. |
| Scheduler | `core/scheduler` + `app/worker.py` | `ScheduledTask`s declared in manifests; rows in `core_scheduled_task`; the worker leases due tasks with `FOR UPDATE SKIP LOCKED` + `lease_until` (safe with several replicas, crash-tolerant). "Run now" = set `next_run_at`. No Redis. |
| Logging | `core/logging.py` | structlog JSON, correlation ID (`X-Request-ID`), secret redaction. |

Planned core services (later phases): workflow engine (Job/Step, reusing the scheduler's leasing
pattern), notifications.

## Module 1 – IT Lifecycle (Phase 2)

```
worker ──every 30 min / "Sync now"──► lifecycle.sync
   SDP (Read): RTR requests (requester filter, look-back) → detail only when changed → parser
               users (employee-ID field configurable) · assets (product types configurable)
   BioStar (Read): users (paged)
   → lifecycle_snapshot (per run)  → reconcile() pure rules → lifecycle_recon_row (per run)
API/UI: latest successful run · filters · detail · acknowledge (lifecycle_issue_ack) · CSV/XLSX
```

- A source that cannot be read marks the run *partial*; rules depending on it are skipped.
- Request descriptions are parsed with configurable labels; change types map to
  `onboard | probation_to_official | transfer | offboard` (explicit mapping first, keyword fallback).
- Assets are owned by SDP users; the owner is linked to an employee via the SDP user's employee-ID
  field, falling back to a unique name match (flagged in the UI).

## Module boundaries

- A module lives in `backend/app/modules/<id>/` and `frontend/src/modules/<id>/`.
- It imports only `app.core.*`. `import-linter` (CI) enforces: modules never import each other,
  core never imports modules.
- Tables are prefixed `<id>_` and migrations live in the module's own Alembic branch.
- Modules are enabled with `INFRAOPS_ENABLED_MODULES`; a disabled module keeps its data but
  exposes no routes, nav, permissions or widgets.

See [adding-a-module.md](adding-a-module.md).

### Handover / return records (Phase 3)

```
prefill (RTR request + latest asset snapshot) → draft (editable, PDF preview with "bản nháp")
  → finalize: number + DOCX/PDF stored, template version pinned, data frozen
  → signed: scan uploaded (PDF/PNG/JPEG) → optional: attach scan to the SDP request (Write credential)
  → void (reason; numbered records need lifecycle.templates.manage)
```

Templates come from the company forms (ITD-FRM-AST-001/002) via `scripts/build_form_templates.py`
(record number in the header, one repeating asset row, Tình trạng column on returns, ☑/☐ checklist,
fixed table layout).

### Onboard / Offboard jobs (Phase 4)

```
sync → draft job per RTR request (no external writes) → Operator prepares (assets, SDP user) → preview
Onboard : check request → find SDP user (waits for AD→SDP import) → assign In Store assets → create BioStar
          user → enrolment (manual, verified on BioStar) → handover record until signed → attach + note + close
Offboard: check request → find SDP user → collect assets + condition (manual) → assets back to In Store
          → disable/delete BioStar user (retype employee ID) → return record until signed → attach + note + close
```

Writes use the Write credential of each connection; SDP writes send `input_data`; nothing is retried
automatically except reads, and each step re-reads state so a manual retry never duplicates changes.

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
  API, worker and web Deployments, a pre-install/upgrade migration Job, Ingress, optional internal CA bundle.
- Health: `/health/live` (process) and `/health/ready` (database).

## Integration status

| System | Status |
|---|---|
| ServiceDesk Plus (on-prem authtoken / cloud Zoho OAuth) | Connector + test (Phase 1); read requests/users/assets (Phase 2). |
| Suprema BioStar 2 | Connector + test with session handling (Phase 1); read users (Phase 2). |
| Microsoft Entra ID, Active Directory, Exchange hybrid | Deferred by decision (see phase-0-proposal §11–12). Registry ready. |
