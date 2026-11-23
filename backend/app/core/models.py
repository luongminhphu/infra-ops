"""Import every core model so Alembic and the app see the full metadata."""

from app.core.audit.models import AuditLog
from app.core.auth.models import User, UserSession
from app.core.connections.models import ConnectionProfile, Credential
from app.core.documents.models import Document, DocumentSequence, TemplateVersion
from app.core.rbac.models import KnownPermission, Role, RolePermission, UserRole
from app.core.scheduler.models import ScheduledTaskRow
from app.core.settings.models import ModuleSetting
from app.core.storage.models import StoredFile
from app.core.workflow.models import Job, JobEvent, JobEvidence, JobStep, WorkflowChecklist

__all__ = [
    "AuditLog",
    "ConnectionProfile",
    "Credential",
    "Document",
    "DocumentSequence",
    "Job",
    "JobEvent",
    "JobEvidence",
    "JobStep",
    "KnownPermission",
    "ModuleSetting",
    "Role",
    "RolePermission",
    "ScheduledTaskRow",
    "StoredFile",
    "TemplateVersion",
    "User",
    "UserRole",
    "UserSession",
    "WorkflowChecklist",
]
