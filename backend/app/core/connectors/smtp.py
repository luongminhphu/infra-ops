"""SMTP relay used to send the "Việc tiếp theo" digest (stdlib smtplib in a worker thread).

The transport is a module-level factory so tests substitute a fake and never open a socket.
``test_connection`` never issues MAIL/RCPT: it only connects, EHLO, STARTTLS, AUTH, NOOP and QUIT.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.connectors.base import (
    AUTH_TYPES,
    AuthField,
    AuthTypeSpec,
    ConnectionContext,
    ConnectorType,
    TestReport,
    TestStep,
)
from app.core.connectors.registry import register
from app.core.errors import AppError

# Anonymous relays are common internally, so both fields are optional.
AUTH_TYPES.setdefault(
    "smtp_login",
    AuthTypeSpec(
        "smtp_login",
        "Đăng nhập SMTP (bỏ trống nếu máy chủ không yêu cầu)",
        (
            AuthField("username", "Tên đăng nhập", secret=False, required=False),
            AuthField("password", "Mật khẩu", secret=True, required=False),
        ),
    ),
)

# Replaced by tests. Called as factory(host, port, timeout=seconds) and used like smtplib.SMTP.
smtp_factory: Any = smtplib.SMTP
smtp_ssl_factory: Any = smtplib.SMTP_SSL  # tls_mode "ssl" (implicit TLS, usually port 465)


class SmtpConfig(BaseModel):
    host: str = Field(title="Máy chủ SMTP", min_length=1, max_length=255)
    port: int = Field(587, title="Cổng", ge=1, le=65535)
    tls_mode: Literal["starttls", "ssl", "none"] = Field(
        "starttls", title="Mã hoá kênh (starttls / ssl / none)"
    )  # "none" is refused when a username is set (no password over plain text)
    from_address: str = Field(title="Địa chỉ người gửi", min_length=3, max_length=320)


class SmtpError(Exception):
    """``code`` is a stable machine code; the server's own text (may echo addresses) is never kept."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# Codes that prove nothing was sent (failure before the message was handed over): the caller may retry later.
SYSTEM_CODES = frozenset({"smtp_auth", "smtp_connect", "smtp_tls", "smtp_insecure_auth"})


def _code(exc: Exception) -> str:
    # SMTPException subclasses OSError, so it must be tested first.
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "smtp_auth"
    if isinstance(exc, smtplib.SMTPRecipientsRefused | smtplib.SMTPSenderRefused):
        return "smtp_refused"
    if isinstance(exc, smtplib.SMTPNotSupportedError | ssl.SSLError):
        return "smtp_tls"
    if isinstance(exc, smtplib.SMTPException):
        return "smtp_error"
    if isinstance(exc, TimeoutError | OSError):
        return "smtp_connect"
    if isinstance(exc, ValueError):
        return "smtp_invalid_message"
    return "smtp_error"


def _insecure_auth() -> AppError:
    return AppError(
        "Không đăng nhập SMTP trên kênh không mã hoá. Chọn STARTTLS hoặc SSL, hoặc bỏ tên đăng nhập.",
        code="smtp_insecure_auth",
    )


def _connect(ctx: ConnectionContext) -> smtplib.SMTP:
    cfg: SmtpConfig = ctx.config  # type: ignore[assignment]
    factory = smtp_ssl_factory if cfg.tls_mode == "ssl" else smtp_factory
    return factory(cfg.host, cfg.port, timeout=ctx.timeout)  # type: ignore[no-any-return]


def _open(ctx: ConnectionContext) -> smtplib.SMTP:
    cfg: SmtpConfig = ctx.config  # type: ignore[assignment]
    user = ctx.credential("username")
    if user and cfg.tls_mode == "none":
        raise _insecure_auth()
    conn = _connect(ctx)
    try:
        conn.ehlo()
        if cfg.tls_mode == "starttls":
            conn.starttls(context=ssl.create_default_context())
            conn.ehlo()
        if user:
            conn.login(user, ctx.credential("password"))
    except BaseException:
        conn.close()
        raise
    return conn


def _send_sync(ctx: ConnectionContext, msg: EmailMessage) -> None:
    try:
        conn = _open(ctx)
    except AppError:
        raise
    except Exception as exc:
        raise SmtpError(_code(exc)) from None  # nothing was handed over yet
    try:
        conn.send_message(msg)
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused):
        raise SmtpError("smtp_refused") from None
    except (smtplib.SMTPResponseException, ValueError):  # the server answered: definitely not accepted
        raise SmtpError("smtp_error") from None
    except Exception:  # timeout / disconnect mid-send: the outcome is unknown, so the caller must not retry
        raise SmtpError("smtp_unknown") from None
    finally:
        try:
            conn.quit()
        except Exception:
            conn.close()


async def send_mail(ctx: ConnectionContext, *, to: str, subject: str, text: str, html: str) -> None:
    """Send one message; raises :class:`SmtpError` with a stable code only."""
    cfg: SmtpConfig = ctx.config  # type: ignore[assignment]
    msg = EmailMessage()
    try:
        msg["From"], msg["To"], msg["Subject"] = cfg.from_address, to, subject
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
    except ValueError:
        raise SmtpError("smtp_invalid_message") from None
    await asyncio.to_thread(_send_sync, ctx, msg)


def _probe(ctx: ConnectionContext) -> TestReport:
    cfg: SmtpConfig = ctx.config  # type: ignore[assignment]
    report = TestReport()
    try:
        if ctx.credential("username") and cfg.tls_mode == "none":
            raise _insecure_auth()
        conn = _connect(ctx)
    except AppError as exc:
        report.add(
            TestStep("auth", "Đăng nhập SMTP", "fail", exc.message, "Đổi mã hoá sang STARTTLS hoặc SSL.")
        )
        return report
    except Exception as exc:
        report.add(
            TestStep(
                "connect",
                "Kết nối SMTP",
                "fail",
                f"Không kết nối được {cfg.host}:{cfg.port} ({_code(exc)}).",
                "Kiểm tra máy chủ, cổng và firewall.",
            )
        )
        return report
    try:
        conn.ehlo()
        report.add(TestStep("connect", "Kết nối SMTP", "ok", f"{cfg.host}:{cfg.port}"))
        if cfg.tls_mode == "starttls":
            conn.starttls(context=ssl.create_default_context())
            conn.ehlo()
            report.add(TestStep("tls", "STARTTLS", "ok"))
        elif cfg.tls_mode == "ssl":
            report.add(TestStep("tls", "SSL", "ok", "TLS ngay khi kết nối."))
        else:
            report.add(
                TestStep("tls", "Mã hoá", "warn", "Không mã hoá.", "Nên dùng STARTTLS nếu máy chủ hỗ trợ.")
            )
        user = ctx.credential("username")
        if user:
            conn.login(user, ctx.credential("password"))
            report.add(TestStep("auth", "Đăng nhập SMTP", "ok"))
        else:
            report.add(TestStep("auth", "Đăng nhập SMTP", "skip", "Không dùng tài khoản."))
        conn.noop()
        report.add(TestStep("noop", "Máy chủ phản hồi", "ok", "Không gửi thư thử."))
    except Exception as exc:
        report.add(
            TestStep(
                "smtp",
                "Phiên SMTP",
                "fail",
                f"Máy chủ từ chối hoặc ngắt kết nối ({_code(exc)}).",
                "Kiểm tra mã hoá, tên đăng nhập / mật khẩu và quyền relay.",
            )
        )
    finally:
        try:
            conn.quit()
        except Exception:
            conn.close()
    return report


@register
class SmtpConnector(ConnectorType):
    system_type = "smtp"
    display_name = "Máy chủ gửi thư (SMTP)"
    description = "Gửi thư tóm tắt “Việc tiếp theo” theo lịch."
    icon = "mail"
    base_url_hint = "Không dùng; nhập https://<máy chủ SMTP>. Máy chủ và cổng thật khai ở phần cấu hình."
    config_model = SmtpConfig
    auth_types = ("smtp_login",)
    credential_bound_config = ("host", "port", "tls_mode")

    async def test_connection(self, ctx: ConnectionContext) -> TestReport:
        return await asyncio.to_thread(_probe, ctx)
