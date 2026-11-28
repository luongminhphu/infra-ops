"""Permission codes owned by the platform core (leaf module: imports nothing from ``app``).

The core manifest that declares them lives in ``app.core.core_manifest``.
"""

DASHBOARD_VIEW = "core.dashboard.view"
CONNECTIONS_VIEW = "core.connections.view"
CONNECTIONS_MANAGE = "core.connections.manage"
CONNECTIONS_TEST = "core.connections.test"
AUDIT_VIEW = "core.audit.view"
RBAC_MANAGE = "core.rbac.manage"
CHECKLIST_MANAGE = "core.workflow.checklist.manage"
BRANDING_MANAGE = "core.branding.manage"
NOTIFICATIONS_MANAGE = "core.notifications.manage"
