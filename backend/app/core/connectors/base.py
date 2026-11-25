"""Connector plugin contract.

A connector type describes one kind of external system: its non-secret configuration
schema (rendered as a form in Settings), the authentication methods it supports, and
how to test a connection. Adding a new system (vCenter, Veeam…) means adding one
connector class and registering it – no change to the core UI or API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel


class Purpose(StrEnum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class AuthField:
    name: str
    label: str
    secret: bool
    required: bool = True
    multiline: bool = False
    help: str = ""


@dataclass(frozen=True)
class AuthTypeSpec:
    key: str
    label: str
    fields: tuple[AuthField, ...]

    @property
    def secret_fields(self) -> list[AuthField]:
        return [f for f in self.fields if f.secret]

    @property
    def public_fields(self) -> list[AuthField]:
        return [f for f in self.fields if not f.secret]


AUTH_TYPES: dict[str, AuthTypeSpec] = {
    spec.key: spec
    for spec in [
        AuthTypeSpec("api_key", "API key / Auth token", (AuthField("api_key", "API key", secret=True),)),
        AuthTypeSpec(
            "username_password",
            "Tên đăng nhập / mật khẩu",
            (
                AuthField("username", "Tên đăng nhập", secret=False),
                AuthField("password", "Mật khẩu", secret=True),
            ),
        ),
        AuthTypeSpec(
            "oauth2_client_credentials",
            "OAuth2 client credentials",
            (
                AuthField("client_id", "Client ID", secret=False),
                AuthField("client_secret", "Client secret", secret=True),
            ),
        ),
        AuthTypeSpec(
            "oauth2_refresh_token",
            "OAuth2 refresh token",
            (
                AuthField("client_id", "Client ID", secret=False),
                AuthField("client_secret", "Client secret", secret=True),
                AuthField("refresh_token", "Refresh token", secret=True),
            ),
        ),
        AuthTypeSpec(
            "certificate",
            "Chứng chỉ (.pem)",
            (
                AuthField("client_id", "Client ID / tên đăng nhập", secret=False, required=False),
                AuthField(
                    "certificate_pem",
                    "Chứng chỉ + khoá riêng (PEM)",
                    secret=True,
                    multiline=True,
                    help="Dán nội dung PEM gồm cả CERTIFICATE và PRIVATE KEY.",
                ),
                AuthField("certificate_password", "Mật khẩu khoá riêng", secret=True, required=False),
            ),
        ),
    ]
}

StepStatus = Literal["ok", "warn", "fail", "skip"]


@dataclass
class TestStep:
    __test__ = False  # not a pytest class
    key: str
    label: str
    status: StepStatus
    detail: str = ""
    hint: str = ""


@dataclass
class PermissionCheck:
    name: str
    granted: bool | None  # None = could not be verified without side effects
    detail: str = ""


@dataclass
class TestReport:
    __test__ = False  # not a pytest class
    steps: list[TestStep] = field(default_factory=list)
    permissions: list[PermissionCheck] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(s.status == "fail" for s in self.steps)

    def add(self, step: TestStep) -> TestStep:
        self.steps.append(step)
        return step

    def failed(self) -> bool:
        return bool(self.steps) and self.steps[-1].status == "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "steps": [s.__dict__ for s in self.steps],
            "permissions": [p.__dict__ for p in self.permissions],
            "info": self.info,
        }


@dataclass(frozen=True)
class ConnectionContext:
    """Everything a connector needs at runtime. Secrets are decrypted only for the call."""

    base_url: str
    config: BaseModel
    purpose: Purpose
    auth_type: str
    public: dict[str, Any]
    secrets: dict[str, Any]
    timeout: float = 20.0

    def credential(self, name: str) -> str:
        value = self.secrets.get(name, self.public.get(name))
        return "" if value is None else str(value)


class ConnectorType(ABC):
    system_type: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str]
    icon: ClassVar[str] = "plug"
    # People may enter their own credential (e.g. a technician API key) used for the work they do.
    personal_credentials: ClassVar[bool] = False
    base_url_hint: ClassVar[str] = ""
    config_model: ClassVar[type[BaseModel]]
    auth_types: ClassVar[tuple[str, ...]]

    @abstractmethod
    async def test_connection(self, ctx: ConnectionContext) -> TestReport:
        """Check DNS, TLS, authentication and actual permissions. Must not modify data."""

    def describe(self) -> dict[str, Any]:
        return {
            "system_type": self.system_type,
            "display_name": self.display_name,
            "description": self.description,
            "icon": self.icon,
            "base_url_hint": self.base_url_hint,
            "config_schema": self.config_model.model_json_schema(),
            "auth_types": [
                {
                    "key": AUTH_TYPES[k].key,
                    "label": AUTH_TYPES[k].label,
                    "fields": [f.__dict__ for f in AUTH_TYPES[k].fields],
                }
                for k in self.auth_types
            ],
        }
