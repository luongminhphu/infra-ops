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
from app.core.modules.manifest import DashboardWidget, ModuleManifest, NavItem, Permission
from app.modules.assets.api import router

manifest = ModuleManifest(
    id="assets",
    name="Tài sản & gia hạn",
    permissions=[
        Permission("assets.renewal.view", "Xem lịch gia hạn", ("manager", "operator", "viewer")),
        Permission("assets.renewal.manage", "Cập nhật lịch gia hạn"),
    ],
    routers=[router],                      # mounted under /api/assets
    # 5th argument (optional): heading in the module sub-navigation; the first item is where the module opens.
    nav=[NavItem("Gia hạn dịch vụ", "/assets/renewals", "calendar-clock", "assets.renewal.view", "Việc cần làm")],
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
