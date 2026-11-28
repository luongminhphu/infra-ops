# Adding a module

Example: an `assets` module for asset/service renewals.

## 1. Backend

```
backend/app/modules/assets/
├── __init__.py
├── manifest.py      # required
├── models.py        # optional – SQLAlchemy models, tables prefixed "assets_"
└── api.py           # optional – FastAPI routers
```

`manifest.py`:

```python
from app.core.modules.manifest import NAV_WORK, DashboardWidget, ModuleManifest, NavItem, Permission
from app.modules.assets.api import router

manifest = ModuleManifest(
    id="assets",
    name="Tài sản & gia hạn",
    permissions=[
        Permission("assets.renewal.view", "Xem lịch gia hạn", ("manager", "operator", "viewer")),
        Permission("assets.renewal.manage", "Cập nhật lịch gia hạn"),
    ],
    routers=[router],                      # mounted under /api/assets
    # 5th argument: the shared menu heading (NAV_WORK, NAV_ASSETS, NAV_SOURCES, NAV_SETUP under Công cụ;
    # NAV_STATS, NAV_REVIEWS under Báo cáo). File the page by what it is for, not by module; core orders headings.
    nav=[NavItem("Gia hạn dịch vụ", "/assets/renewals", "calendar-clock", "assets.renewal.view", NAV_WORK)],
    connectors_used=["sdp"],               # shown as "Dùng bởi" on connection profiles
    dashboard_widgets=[],                  # DashboardWidget(key, title, kind, permission, load)
    scheduled_tasks=[                      # run by the worker: python -m app.worker
        ScheduledTask("assets.sync", "Đọc lịch gia hạn", handler=sync_renewals, interval_seconds=3600),
    ],
    migrations_branch="assets",
)
```

Printable records are declared as `DocumentKind`s (see `app/modules/lifecycle/documents.py`):

```python
RENEWAL = DocumentKind(
    key="assets.renewal", title="Phiếu gia hạn", prefix="GH",
    default_template=Path(__file__).parent / "templates/renewal.docx",
    data_model=RenewalData, build_context=lambda data, number: {...}, subject=lambda d: (d.id, d.name, None),
    placeholders=[PlaceholderDoc("number", "Số phiếu")], sample_data={...},
    view_permission="assets.renewal.view", create_permission="assets.renewal.manage",
    manage_permission="assets.renewal.manage",
)
# manifest: document_kinds=[RENEWAL] – core provides templates, numbering, PDF, signed upload, APIs.
```

Data a Playbook check step can test (e.g. "assets in use of type X") is declared by the module that owns it:

```python
FactSource("assets.renewals_due", "Số dịch vụ sắp hết hạn", "number", load_renewals_due)
# async load(session, employee_id, param) -> FactValue(value, source) | None  (None = unknown, never a guess)
# manifest: facts=[...]; keys start with the module id.
```

Records the global search (Ctrl+K) should find are declared like the "Việc tiếp theo" queue (`NextActionSource`): a `SearchSource` per
entity, in the manifest (`search_sources=[...]`; keys start with the module id):

```python
async def search_renewals(session, user, q, limit):
    # Same row scope as the list endpoint, and only fields that list shows to this user (never hidden columns / prices).
    hay = func.concat_ws(" ", Renewal.name, Renewal.owner)
    rows = await session.scalars(
        select(Renewal).where(fold_sql(hay).contains(fold(q), autoescape=True)).limit(limit)
    )
    return [{"title": r.name, "detail": r.owner, "href": f"/assets/renewals?open={r.id}", "kind": "renewal"} for r in rows]

SearchSource("assets.renewals", "assets.renewal.view", "Gia hạn", search_renewals)
```

Core calls it only for users holding `permission`, only from 2 characters, with a 2 s timeout (a slow or failing source is reported, not
fatal), `limit` = 5. `fold` / `fold_sql` come from `app.core.text` (accent-insensitive: "nguyen" finds "Nguyễn"). Use stable page paths for `href`.

Printable records of a kind that has a Job behind it (or an owner) can restrict who sees them: `DocumentKind(view_all_permission="…")`.
Without that permission (and without `manage_permission`) people see only records they created or whose Job they may see – `create_draft(…, job_id=job.id)`
links the Job. `None` (default) = everyone with `view_permission` sees all.

Exports to Excel go through `app.core.xlsx`: wrap every user/system-derived string in `safe(...)` (formula injection) and write the header with
`header(ws, headers, widths)`; limit the row count and audit the export.

Every module nav page is also a *tool* a Playbook step can open; its key is the path without slashes
(`/assets/renewals` → `assets.renewals`), so keep page paths stable.

Module settings (mappings, thresholds) are a pydantic model with defaults, persisted by core:

```python
from app.core.settings.service import load_settings, save_settings

class AssetsSettings(BaseModel):
    warn_days: int = Field(30, ge=1, le=365)

settings = await load_settings(session, "assets", "main", AssetsSettings)
await save_settings(session, user.actor, "assets", "main", AssetsSettings, body)  # validated + audited
```

Rules:
- Permission codes and scheduled task keys must start with the module id.
- Never call external systems inside an API request for bulk reads: schedule a task and let the
  UI request a run (`core.scheduler.service.request_run`).
- Import only from `app.core.*`. Get external-system access with
  `app.core.connections.service.open_connection(session, "sdp", Purpose.READ)`.
- Protect endpoints with `Depends(require("assets.renewal.view"))`.
- Record changes with `app.core.audit.service.record(...)` (values are redacted automatically).
- Raise `AppError`/`NotFound`/`Conflict` with Vietnamese user-facing messages.

Add the contract to `backend/pyproject.toml`:

```toml
[[tool.importlinter.contracts]]
name = "Modules must not import each other"
type = "independence"
modules = ["app.modules.lifecycle", "app.modules.assets"]
```

## 2. Migrations (own Alembic branch)

1. Add `%(here)s/migrations/assets/versions` to `version_locations` in `backend/alembic.ini`.
2. Create the branch:

```bash
cd backend
uv run alembic revision --autogenerate -m "assets schema" \
  --head base --branch-label assets --depends-on core@head \
  --version-path "$PWD/migrations/assets/versions"
```

3. `uv run alembic upgrade heads`. Later revisions: `--head assets@head`.

Never edit another module's migrations.

## 3. Frontend

```
frontend/src/modules/assets/
├── index.ts         # export const assetsModule: FrontendModule = { id: "assets", routes: [...] }
└── pages/…
```

Register it in `frontend/src/modules/index.ts`. To render a dashboard widget declared in the
manifest, add `widgets: { "<kind>": Component }` to the module object. Navigation entries come from the backend manifest,
so the sidebar updates automatically. Use core components (`@/core/ui/*`) and tokens only – see
`docs/design-system.md`. Wrap pages in `<RequirePermission permission="assets.renewal.view">`.
If the backend icon name is new, map it in `frontend/src/core/ui/icons.ts`.

**Job page panels.** A module can add a panel to the Job page of any workflow without the Job's owner importing it:
`jobPanels: [{ key, order, permission, title, component }]` on the `FrontendModule`. The Job page (lifecycle) reads them
through `useModules()`, keeps those the user `can(permission)` and renders them in `order` (wizard: collapsible, below the
current step; full view: a section). The component receives `{ jobId, workflowKey, status, version, collapsible? }` and
renders nothing when it has nothing to say. **`version` changes whenever the Job changes – use it in the query key** so
the panel refetches. Example: Playbook's `JobPlaybookPanel` (`playbook.guides.view`).

On the backend, a workflow whose `subject_key` is an employee ID sets `WorkflowDefinition.subject_is_employee=True`; modules
that load employee facts for a Job (e.g. the Playbook run) do so only for such workflows.

## 4. New external system? Add a connector

```python
# backend/app/core/connectors/vcenter.py
@register
class VCenterConnector(ConnectorType):
    system_type = "vcenter"
    display_name = "VMware vCenter"
    description = "…"
    config_model = VCenterConfig          # pydantic; becomes the Settings form
    auth_types = ("username_password",)

    async def test_connection(self, ctx): ...  # DNS → TLS → auth → permission probes, no writes
```

Import it in `load_builtin_connectors()`. Tests mock HTTP with `respx`; never call real systems.

## 5. Enable

```
INFRAOPS_ENABLED_MODULES=lifecycle,assets
```

Restart: new permissions are registered and granted to their default roles once.

## Checklist

- [ ] Manifest permissions prefixed and described in Vietnamese
- [ ] Endpoints protected by `require(...)`, mutations audited
- [ ] Own Alembic branch; `import-linter` contract updated
- [ ] Loading / empty / error / success states; axe scan clean in light and dark
- [ ] Tests (unit + API, mocked integrations)
- [ ] README/CLAUDE.md updated if conventions change
