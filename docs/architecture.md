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
| RBAC | `core/rbac` | Permission codes `<module>.<resource>.<action>` declared by manifests. System roles Admin (all permissions, locked), Manager, Supervisor, Operator, Viewer; a first SSO login gets Viewer. Default grants applied once, the first time a permission appears. |
| Audit | `core/audit` | Append-only table (DB trigger rejects UPDATE/DELETE/TRUNCATE) + SHA-256 hash chain, verifiable from the UI. Values are redacted before storage. |
| Secrets | `core/secrets` | AES-256-GCM, versioned keyring (`INFRAOPS_MASTER_KEYS`), AAD binds ciphertext to `credential:<profile>:<purpose>`. |
| Connections | `core/connections` | Connection Profiles (non-secret config, validated by the connector schema) + Credentials per purpose (`read` / `write`). `open_connection()` is the only way modules get credentials. |
| Connectors | `core/connectors` | Plugin registry. Each connector declares config schema, auth types and `test_connection()` (DNS → TLS → auth → real permission probes). Shared HTTP client: timeouts, retry with backoff + jitter, `Retry-After`. |
| Dashboard | `core/dashboard` | Widgets declared by manifests, filtered by permission; a failing widget does not break the page. Modules provide the renderer for their widget `kind` in the frontend. |
| Module settings | `core/settings` | Typed (pydantic) per-module configuration in `core_module_setting`; invalid stored values fall back to defaults; changes audited as diffs. |
| Files | `core/storage` | `core_file` metadata + `LocalFileStorage` (directory / PVC; S3 later). Uploads are type-checked by magic bytes and size-limited. |
| Documents | `core/documents` | `DocumentKind`s declared by modules. Versioned DOCX templates (bundled default seeded as v1; uploads validated, unknown placeholders flagged), rendering with docxtpl (autoescape), PDF via LibreOffice with an isolated profile per conversion, monthly numbering `PREFIX-YYYYMM-####` (atomic upsert), lifecycle draft → finalized → signed / void. **Row scope:** `core_document.job_id` links a record to its Job; a kind with `view_all_permission` shows people without that permission (or `manage_permission`) only records they created or whose Job they may see (`scope_clause`, same rule as `check_job_scope`); out-of-scope records answer 404. Editing a draft needs the creator, `manage_permission`, or whoever runs the Job (`can_edit_draft`). |
| Workflow | `core/workflow` | Generic Job/Step engine. Workflows declared in manifests; the worker (`core.workflow` task, every 15 s or on demand) advances jobs under row locks: automatic steps, manual steps (prepare + validate input), typed confirmation, waits with timers/timeouts, failure + retry, skip, cancel, admin override-close, dry-run preview. Job log (`core_job_event`) + audit with before/after state. Workflows with `verify_steps` stop each performed step in `verify` until an operator ticks its checklist (versioned per workflow in `core_workflow_checklist`, snapshotted on the job at start) and attaches evidence where required (`core_job_evidence`: image/file via core storage, link, note); rejecting fails the step for a retry. |
| Scheduler | `core/scheduler` + `app/worker.py` | `ScheduledTask`s declared in manifests; rows in `core_scheduled_task`; the worker leases due tasks with `FOR UPDATE SKIP LOCKED` + `lease_until` (safe with several replicas, crash-tolerant). "Run now" = set `next_run_at`. No Redis. |
| Text | `core/text.py` | `fold(text)` (lower-case, Vietnamese diacritics stripped, đ → d) and `fold_sql(col)` (its PostgreSQL twin via `translate`, no extension). Match with `fold_sql(col).contains(fold(q), autoescape=True)`. |
| Spreadsheets | `core/xlsx.py` | `safe(value)` neutralises formula injection (leading `= + - @` TAB CR gets an apostrophe) and `header(ws, headers, widths)` writes the standard header row. Every export uses them. |
| Search | `core/search` | `GET /api/search?q=` (Ctrl+K). Fans out over the modules' `SearchSource`s (`ModuleManifest.search_sources`), sequentially on one session, each wrapped in a 2 s timeout; a failing source is reported in `failed`, never as a raw error. Core declares the Job and Document sources (`core/search/sources.py`). Each source applies the permission and row scope of its entity's list endpoint and matches only fields that list shows. |
| Notifications | `core/notifications` | Scheduled digest of "Việc tiếp theo" (task `core.notifications`, every 5 min): opt-in per person (`core_notification_pref`) plus a master switch in settings; e-mail per person through the `smtp` connector, team totals through the `teams` webhook connector. A log row (`core_notification_sent`, unique per person/channel/slot) is committed before sending, so a crash never causes a duplicate; `core_notification_item` remembers the priority/title last mailed so an item is repeated only when it becomes more urgent or retitled. Content is computed with the recipient's own permissions. |
| Job panels | `frontend/src/core/modules.ts` | `FrontendModule.jobPanels` slot: a module adds a panel to the Job page (props `{jobId, workflowKey, status, version, collapsible?}`) without the Job page importing it; Playbook uses it to show the guide inside a Job (Phase 74). `WorkflowDefinition.subject_is_employee` marks workflows whose subject is an employee ID. |
| Logging | `core/logging.py` | structlog JSON, correlation ID (`X-Request-ID`), secret redaction. |

Planned core services (later phases): none open; notifications shipped in Phase 73.

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
- Frontend: the same rules, plus modules never import `@/core/pages` (shared pieces live in `core/lib`
  and `core/ui`). Only `src/app/App.tsx` imports `@/modules`; core reads the list with `useModules()`.
  Enforced by `src/core/boundaries.test.ts` (vitest, CI).
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

Each job has an assignee (the RTR request's SDP technician, refreshed on sync). Reassigning runs the
`lifecycle.assign` workflow in the worker, which writes the technician to SDP first; every change is a
`core_job_assignment` row (from/to, step, source `sdp`|`app`, actor, reason) drawn as swimlanes in the UI.

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
| Microsoft Entra ID, Active Directory, Exchange hybrid | Phase 69: connectors `active_directory` (LDAPS), `exchange` (WinRM), `msgraph` (Graph); used by onboard/offboard steps when `microsoft_enabled` is on (phase-0-proposal §69). Tested with fakes only. |
