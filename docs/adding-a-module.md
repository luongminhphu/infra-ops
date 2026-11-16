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
        Permission("assets.renewal.view", "Xem lịch gia hạn", ("helpdesk", "viewer")),
        Permission("assets.renewal.manage", "Cập nhật lịch gia hạn"),
    ],
    routers=[router],                      # mounted under /api/assets
    nav=[NavItem("Gia hạn dịch vụ", "/assets/renewals", "calendar-clock", "assets.renewal.view")],
    connectors_used=["sdp"],               # shown as "Dùng bởi" on connection profiles
    dashboard_widgets=[],                  # DashboardWidget(key, title, kind, permission, load)
    migrations_branch="assets",
)
```

Rules:
- Permission codes must start with the module id.
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

Register it in `frontend/src/modules/index.ts`. Navigation entries come from the backend manifest,
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
