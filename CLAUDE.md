# CLAUDE.md – infra-ops

Internal IT operations platform: Platform Core + pluggable Modules. Read `docs/architecture.md`
and `docs/adding-a-module.md` before structural changes; `docs/design-system.md` before any UI work.

## Layout

- `backend/app/core/*` – platform core. `backend/app/modules/<id>/` – modules (manifest.py required).
- `frontend/src/core/*` – shell, design system (`ui/`), core pages. `frontend/src/modules/<id>/` – module UI.
- `backend/migrations/<branch>/versions` – one Alembic branch per module (`core`, …).
- `docs/phase-0-proposal.md` – decisions and open questions; add a section per phase (latest: §25).

## Conventions

- Code, comments, commit messages: English. UI text: Vietnamese. Dates `dd/MM/yyyy`, tz Asia/Ho_Chi_Minh.
- Modules import only `app.core.*` (enforced by `uv run lint-imports`). Core never imports modules.
- Tables prefixed `core_` / `<module>_`. Never edit another module's migrations.
- Permissions: `<module>.<resource>.<action>`, declared in the manifest; protect endpoints with `require()`.
  System roles: `admin` (all), `manager`, `supervisor`, `operator`, `viewer` – use them as `default_roles`.
  Row-level scoping (e.g. operators see only their own tasks) is enforced in the API with a `*.view_all` permission.
- Every state change by a user: `audit.record(...)` in the same transaction. Never log or return secrets.
- External systems only via `open_connection(session, system_type, Purpose.READ|WRITE)`; credentials
  never come from env. Connectors must not write data in `test_connection()`.
- Errors: raise `AppError` subclasses with a Vietnamese message + stable English `code`.
- Module configuration: a pydantic model persisted with `core.settings.service` (load/save, audited);
  periodic work: `ScheduledTask` in the manifest, run by the worker (never inside API requests).
- Reconciliation rules are pure functions (`modules/lifecycle/reconcile.py`) – add a rule there with a
  unit test; never judge a system whose read failed.
- Employee IDs are strings (keep leading zeros, e.g. `003551`).
- Job ownership: `Job.assignee_*` + `core_job_assignment` history via `engine.set_assignee` (no-op when
  unchanged). Personal pages are `portal` sections in the frontend module (rendered on `/me/work`).
- Operating guidance in the UI: core `HelpTip` next to the heading/field (phrasing content only: use
  `<span className="block">` for paragraphs). Cross-module UI handoffs go through URLs (e.g. purchases `?new=1&…`).
- Printable records: declare a `DocumentKind` in the manifest (pydantic data model + `build_context`);
  core handles templates (versioned DOCX, docxtpl autoescaped), numbering, PDF (LibreOffice), signed
  uploads (magic-byte checked) and audit. Never hard-code layout in code – it lives in the DOCX.
- Files go through `core.storage.service.save_file/read_file`, never direct filesystem paths.
- Processes: declare a `WorkflowDefinition` (core/workflow) in the manifest. Steps run in the worker,
  never in API requests; give each step a default `checklist` (+ `evidence_required`) when the workflow sets
  `verify_steps=True` (operators confirm every step; managers edit checklists in the UI); every write step must read current state first (idempotent on retry), return
  `before_state`, and never roll back automatically. Dangerous steps use `confirm=True`.
- Tests: edit files with assertions (`assert old in s`) or the Edit tool – silent no-op edits have
  caused bugs; cover each wiring path (e.g. sync → auto-created jobs) with a test.
- Tests never contact real systems (mock with `respx`). Backend tests need PostgreSQL
  (`INFRAOPS_TEST_DATABASE_URL`).
- UI: only design tokens (no raw hex), core components, icon + label for every status, native
  `<dialog>`, field errors after interaction, axe-clean in light and dark.
- Project skills in `.claude/skills/` (vendored upstream, do not edit; update by re-copying):
  `ui-ux-pro-max` (nextlevelbuilder/ui-ux-pro-max-skill – design-system/palette/UX search) and
  `impeccable` (pbakaus/impeccable – design critique/polish). Use them for UI work.
- Microsoft integrations (Entra/AD/Exchange) are deferred by the product owner – do not add them
  without being asked.

## Commands

```bash
# backend
cd backend && uv sync
uv run pytest -q            # needs INFRAOPS_TEST_DATABASE_URL (default: socket /tmp port 5433)
uv run ruff check . && uv run ruff format --check . && uv run lint-imports
uv run alembic upgrade heads
uv run uvicorn app.asgi:app --reload --port 8000
uv run python -m app.worker # scheduled tasks (sync); required for "Sync now"
# PDF tests need LibreOffice (`soffice`); they are skipped when it is missing

# frontend
cd frontend && npm install
npx tsc -b && TZ=UTC npx vitest run && npx vite build
npm run dev                 # proxies /api to :8000
```

## Workflow

- Work phase by phase; stop for review after each phase. Small commits with clear messages.
- Update README.md, this file and docs when conventions or phases change.
