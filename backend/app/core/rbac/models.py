from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Timestamps, UUIDPk


class Role(UUIDPk, Timestamps, Base):
    __tablename__ = "core_role"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # System roles cannot be deleted. ``all_permissions`` (Admin) cannot be edited, preventing lock-out.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    all_permissions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RolePermission(Base):
    __tablename__ = "core_role_permission"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_role.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(String(128), primary_key=True)


class UserRole(Base):
    __tablename__ = "core_user_role"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_user.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_role.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class KnownPermission(Base):
    """Permissions the platform has seen at least once.

    Used so that default grants are applied only the first time a permission appears.
    """

    __tablename__ = "core_permission"

    code: Mapped[str] = mapped_column(String(128), primary_key=True)
    module_id: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
