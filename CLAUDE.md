# CLAUDE.md – infra-ops

Internal IT operations platform: Platform Core + pluggable Modules. Read `docs/architecture.md`
and `docs/adding-a-module.md` before structural changes; `docs/design-system.md` before any UI work.

## Layout

- `backend/app/core/*` – platform core. `backend/app/modules/<id>/` – modules (manifest.py required).
- `frontend/src/core/*` – shell, design system (`ui/`), core pages. `frontend/src/modules/<id>/` – module UI.
- `backend/migrations/<branch>/versions` – one Alembic branch per module (`core`, …).
- `docs/phase-0-proposal.md` – decisions and open questions; keep §11–12 up to date.

## Conventions

- Code, comments, commit messages: English. UI text: Vietnamese. Dates `dd/MM/yyyy`, tz Asia/Ho_Chi_Minh.
- Modules import only `app.core.*` (enforced by `uv run lint-imports`). Core never imports modules.
- Tables prefixed `core_` / `<module>_`. Never edit another module's migrations.
- Permissions: `<module>.<resource>.<action>`, declared in the manifest; protect endpoints with `require()`.
- Every state change by a user: `audit.record(...)` in the same transaction. Never log or return secrets.
- External systems only via `open_connection(session, system_type, Purpose.READ|WRITE)`; credentials
  never come from env. Connectors must not write data in `test_connection()`.
- Errors: raise `AppError` subclasses with a Vietnamese message + stable English `code`.
- Tests never contact real systems (mock with `respx`). Backend tests need PostgreSQL
  (`INFRAOPS_TEST_DATABASE_URL`).
- UI: only design tokens (no raw hex), core components, icon + label for every status, native
  `<dialog>`, field errors after interaction, axe-clean in light and dark.
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

# frontend
cd frontend && npm install
npx tsc -b && TZ=UTC npx vitest run && npx vite build
npm run dev                 # proxies /api to :8000
```

## Workflow

- Work phase by phase; stop for review after each phase. Small commits with clear messages.
- Update README.md, this file and docs when conventions or phases change.
