"""Infrastructure configuration loaded from environment variables.

Only infrastructure settings live here (DB, keys, OIDC). Credentials for external
systems are managed at runtime through Connection Profiles, never through env.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="INFRAOPS_", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    public_base_url: str = "http://localhost:5173"

    database_url: str = "postgresql+asyncpg://infraops:infraops@localhost:5432/infraops"
    database_echo: bool = False

    # Keyring for secret encryption: "v2:<base64 32 bytes>,v1:<base64 32 bytes>".
    # The first entry encrypts new secrets; all entries can decrypt.
    master_keys: SecretStr = SecretStr("")

    # Modules mounted at startup.
    enabled_modules: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["lifecycle", "workload", "playbook"]
    )

    # Authentication
    # "local": username/password accounts managed in the app (interim, until Entra SSO is set up).
    # "oidc": Microsoft Entra ID SSO. "dev": passwordless login for local development only.
    auth_mode: Literal["local", "oidc", "dev"] = "local"
    # With SSO: also accept accounts that have a password in infra-ops (login page shows it as a second option).
    allow_local_login: bool = False
    oidc_tenant_id: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_authority_host: str = "https://login.microsoftonline.com"
    session_cookie_name: str = "infraops_session"
    session_cookie_secure: bool = True
    session_ttl_hours: int = 10
    # Signs short-lived OIDC state cookies.
    signing_secret: SecretStr = SecretStr("")
    # UPNs that receive the Admin role on login (bootstrap only).
    bootstrap_admin_upns: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Local accounts
    local_max_failed_logins: int = 5
    local_lockout_minutes: int = 15
    local_min_password_length: int = 12

    # Files (documents, templates, signed scans)
    storage_dir: str = "./var/files"
    max_upload_mb: int = 20
    soffice_path: str = "soffice"
    pdf_timeout_seconds: int = 90

    # Operations
    secret_expiry_warn_days: int = 30
    connector_timeout_seconds: float = 20.0
    log_level: str = "INFO"
    log_json: bool = True

    _split = field_validator("enabled_modules", "bootstrap_admin_upns", mode="before")(_split_csv)

    @field_validator("enabled_modules")
    @classmethod
    def _renamed_modules(cls, v: list[str]) -> list[str]:
        # "handbook" was renamed "playbook": an older .env keeps working.
        return list(dict.fromkeys("playbook" if m == "handbook" else m for m in v))

    @field_validator("bootstrap_admin_upns")
    @classmethod
    def _lower_upns(cls, value: list[str]) -> list[str]:
        return [v.lower() for v in value]

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        if self.environment == "production":
            if self.auth_mode == "dev":
                raise ValueError("INFRAOPS_AUTH_MODE=dev is not allowed in production")
            if not self.master_keys.get_secret_value():
                raise ValueError("INFRAOPS_MASTER_KEYS is required in production")
            if self.auth_mode == "oidc" and not (self.oidc_tenant_id and self.oidc_client_id):
                raise ValueError("OIDC requires INFRAOPS_OIDC_TENANT_ID and INFRAOPS_OIDC_CLIENT_ID")
            if len(self.signing_secret.get_secret_value()) < 32:
                raise ValueError("INFRAOPS_SIGNING_SECRET must be at least 32 characters in production")
        return self

    @property
    def local_login(self) -> bool:
        """Username/password sign-in is accepted (local mode, or SSO with local accounts allowed)."""
        return self.auth_mode == "local" or (self.auth_mode == "oidc" and self.allow_local_login)

    @property
    def oidc_issuer(self) -> str:
        return f"{self.oidc_authority_host}/{self.oidc_tenant_id}/v2.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
