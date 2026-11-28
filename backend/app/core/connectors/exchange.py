"""Exchange (hybrid, on-prem) via WinRM / PowerShell remoting (pypsrp).

Only an allow-list of cmdlets can run, and parameters are always passed as data
(``add_parameter``) – a script string is never built. pypsrp is synchronous, so every
call runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.core.connectors.base import (
    ConnectionContext,
    ConnectorType,
    PermissionCheck,
    TestReport,
    TestStep,
)
from app.core.connectors.http import ConnectorHttpError
from app.core.connectors.netcheck import check_dns, check_tcp_tls, split_host_port
from app.core.connectors.registry import register
from app.core.logging import get_logger

log = get_logger(__name__)

ALLOWED_CMDLETS = frozenset({"Get-RemoteMailbox", "Enable-RemoteMailbox"})
OBJECT_NOT_FOUND = 13  # PowerShell ErrorCategory.ObjectNotFound


class ExchangeConfig(BaseModel):
    auth: Literal["basic", "ntlm"] = Field("ntlm", title="Kiểu xác thực WinRM")
    configuration_name: str = Field("Microsoft.Exchange", title="Tên cấu hình PowerShell")
    verify_tls: bool = Field(True, title="Kiểm tra chứng chỉ SSL")
    ca_bundle_pem: str = Field("", title="CA nội bộ (PEM)", json_schema_extra={"multiline": True})
    operation_timeout_seconds: int = Field(120, title="Timeout thao tác (giây)", ge=5, le=600)


class ExchangeError(ConnectorHttpError):
    pass


class ExchangeNotFound(ExchangeError):
    pass


def check_transport(base_url: str, auth: str, verify_tls: bool = True) -> None:
    if auth == "basic" and (urlsplit(base_url).scheme != "https" or not verify_tls):
        raise ExchangeError(
            "Xác thực Basic chỉ được dùng với HTTPS và có kiểm tra chứng chỉ.",
            hint="Đổi địa chỉ sang https://, bật kiểm tra chứng chỉ hoặc dùng NTLM.",
        )


def _friendly(exc: Exception) -> ExchangeError:
    """Map a pypsrp/requests failure to a Vietnamese message. Never echoes exception text (may hold secrets)."""
    name = type(exc).__name__
    text = str(exc).lower()
    if "ssl" in name.lower() or "certificate" in text:
        return ExchangeError("Chứng chỉ máy chủ Exchange không hợp lệ.", hint="Thêm CA nội bộ vào cấu hình.")
    if "timeout" in name.lower() or "timed out" in text:
        return ExchangeError(
            "Hết thời gian chờ Exchange.", hint="Kiểm tra firewall/cổng 443 hoặc tăng timeout thao tác."
        )
    if "auth" in name.lower() or "401" in text or "credentials" in text:
        return ExchangeError(
            "Xác thực Exchange thất bại.", hint="Kiểm tra tên đăng nhập, mật khẩu và kiểu xác thực."
        )
    return ExchangeError(f"Lỗi khi gọi Exchange ({name}).", hint="Xem log máy chủ Exchange để biết chi tiết.")


def _is_not_found(rec: Any) -> bool:
    ident = f"{getattr(rec, 'fq_error', '')} {getattr(rec, 'reason', '')}"
    if "ObjectNotFound" in ident or "ManagementObjectNotFoundException" in ident:
        return True
    if getattr(rec, "category", None) == OBJECT_NOT_FOUND:
        return True
    return "couldn't be found" in str(rec)  # last resort: localized text varies


def _props(obj: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for attr in ("adapted_properties", "extended_properties"):
        for k, v in (getattr(obj, attr, None) or {}).items():
            out[str(k)] = "" if v is None else str(v)
    return out or {"value": str(obj)}


class ExchangeClient:
    def __init__(self, ctx: ConnectionContext) -> None:
        self.cfg: ExchangeConfig = ctx.config  # type: ignore[assignment]
        check_transport(ctx.base_url, self.cfg.auth, self.cfg.verify_tls)
        self.ctx = ctx
        self._pool: Any = None
        self._ca_file: str | None = None

    def _open(self) -> None:
        from pypsrp.powershell import RunspacePool
        from pypsrp.wsman import WSMan

        parts = urlsplit(self.ctx.base_url)
        verify: bool | str = self.cfg.verify_tls
        if self.cfg.verify_tls and self.cfg.ca_bundle_pem.strip():
            fd, self._ca_file = tempfile.mkstemp(suffix=".pem")
            with os.fdopen(fd, "w") as fh:
                fh.write(self.cfg.ca_bundle_pem)
            verify = self._ca_file
        op = self.cfg.operation_timeout_seconds
        wsman = WSMan(
            parts.hostname or "",
            port=parts.port or (443 if parts.scheme == "https" else 80),
            ssl=parts.scheme == "https",
            path=parts.path.strip("/") or "PowerShell",
            username=self.ctx.credential("username"),
            password=self.ctx.credential("password"),
            auth=self.cfg.auth,
            cert_validation=verify,
            connection_timeout=int(self.ctx.timeout),
            operation_timeout=op,
            read_timeout=op + 10,
        )
        self._pool = RunspacePool(wsman, configuration_name=self.cfg.configuration_name)
        self._pool.open()

    def _close(self) -> None:
        try:
            if self._pool is not None:
                self._pool.close()
        finally:
            self._pool = None
            if self._ca_file:
                os.unlink(self._ca_file)
                self._ca_file = None

    async def __aenter__(self) -> ExchangeClient:
        try:
            await asyncio.to_thread(self._open)
        except Exception as exc:
            await asyncio.to_thread(self._close)
            raise _friendly(exc) from None
        return self

    async def __aexit__(self, *exc: object) -> None:
        await asyncio.to_thread(self._close)

    def _run(self, cmdlet: str, params: dict[str, Any]) -> list[dict[str, str]]:
        from pypsrp.powershell import PowerShell

        ps = PowerShell(self._pool)
        ps.add_cmdlet(cmdlet)
        for key, value in params.items():
            if value is True:
                ps.add_parameter(key)  # switch
            elif value is not False and value is not None:
                ps.add_parameter(key, value)
        objects = ps.invoke()
        if ps.had_errors:
            errors = list(ps.streams.error)
            if any(_is_not_found(e) for e in errors):
                raise ExchangeNotFound(f"Không tìm thấy đối tượng khi chạy {cmdlet}.")
            text = "; ".join(str(e) for e in errors)[:300]
            raise ExchangeError(f"Exchange báo lỗi khi chạy {cmdlet}: {text}")
        return [_props(o) for o in objects]

    async def run(self, cmdlet: str, params: dict[str, Any]) -> list[dict[str, str]]:
        if cmdlet not in ALLOWED_CMDLETS:
            raise ExchangeError(f"Cmdlet không được phép: {cmdlet}")
        try:
            return await asyncio.to_thread(self._run, cmdlet, params)
        except ExchangeError:
            raise
        except Exception as exc:
            log.warning("exchange_call_failed", cmdlet=cmdlet, error_type=type(exc).__name__)
            raise _friendly(exc) from None

    async def get_remote_mailbox(self, identity: str) -> dict[str, str] | None:
        try:
            rows = await self.run("Get-RemoteMailbox", {"Identity": identity})
        except ExchangeNotFound:
            return None
        return rows[0] if rows else None

    async def enable_remote_mailbox(self, identity: str, remote_routing_address: str | None) -> None:
        params: dict[str, Any] = {"Identity": identity}
        if remote_routing_address:
            params["RemoteRoutingAddress"] = remote_routing_address
        await self.run("Enable-RemoteMailbox", params)


@register
class ExchangeConnector(ConnectorType):
    system_type = "exchange"
    display_name = "Microsoft Exchange (PowerShell từ xa)"
    description = "Tạo remote mailbox cho Microsoft 365 hybrid qua WinRM/PowerShell của Exchange nội bộ."
    icon = "mail"
    base_url_hint = "https://exchange.congty.local/PowerShell"
    config_model = ExchangeConfig
    auth_types = ("username_password",)

    async def test_connection(self, ctx: ConnectionContext) -> TestReport:
        report = TestReport()
        config: ExchangeConfig = ctx.config  # type: ignore[assignment]
        host, port, tls = split_host_port(ctx.base_url)
        if not host or not await check_dns(report, host):
            if not report.steps:
                report.add(TestStep("dns", "Địa chỉ máy chủ", "fail", "Địa chỉ không hợp lệ."))
            return report
        if config.ca_bundle_pem.strip():
            report.add(TestStep("tls", "Kết nối TLS", "skip", "Dùng CA nội bộ: kiểm tra khi mở phiên."))
        else:
            await check_tcp_tls(
                report, host, port, tls=tls, verify=config.verify_tls, connect_timeout=ctx.timeout
            )
            if report.failed():
                return report
        try:
            async with ExchangeClient(ctx) as client:
                report.add(TestStep("auth", "Mở phiên PowerShell", "ok", "Đã xác thực và mở runspace."))
                rows = await client.run("Get-RemoteMailbox", {"ResultSize": 1})
                report.add(
                    TestStep("read", "Đọc remote mailbox", "ok", f"Đọc được ({len(rows)} bản ghi mẫu).")
                )
        except ConnectorHttpError as exc:
            key, label = (
                ("read", "Đọc remote mailbox")
                if report.steps[-1].key == "auth"
                else (
                    "auth",
                    "Mở phiên PowerShell",
                )
            )
            report.add(TestStep(key, label, "fail", exc.message, exc.hint))
            return report
        report.permissions.append(
            PermissionCheck(
                "Enable-RemoteMailbox",
                None,
                "Không kiểm tra được mà không có tác dụng phụ (Get-Command không được phép).",
            )
        )
        return report
