"""Import every core model so Alembic and the app see the full metadata."""

from app.core.audit.models import AuditLog
from app.core.auth.models import User, UserSession
from app.core.connections.models import ConnectionProfile, Credential
from app.core.rbac.models import KnownPermission, Role, RolePermission, UserRole

__all__ = [
    "AuditLog",
    "ConnectionProfile",
    "Credential",
    "KnownPermission",
    "Role",
    "RolePermission",
    "User",
    "UserRole",
    "UserSession",
]
