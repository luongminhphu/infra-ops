"""Low-level reachability checks reported step by step in Test Connection."""

from __future__ import annotations

import asyncio
import socket
import ssl
from datetime import UTC, datetime
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.x509.oid import NameOID

from app.core.connectors.base import TestReport, TestStep


def split_host_port(url: str, default_port: int | None = None) -> tuple[str, int, bool]:
    parts = urlsplit(url if "://" in url else f"https://{url}")
    tls = parts.scheme in ("https", "ldaps")
    port = parts.port or default_port or (443 if tls else 80)
    return parts.hostname or "", port, tls


async def check_dns(report: TestReport, host: str) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        report.add(
            TestStep(
                "dns",
                "Phân giải tên miền (DNS)",
                "fail",
                f"Không phân giải được {host}: {exc.strerror}",
                "Kiểm tra tên máy chủ và DNS nội bộ của cluster.",
            )
        )
        return []
    addrs = sorted({str(i[4][0]) for i in infos})
    report.add(TestStep("dns", "Phân giải tên miền (DNS)", "ok", f"{host} → {', '.join(addrs[:4])}"))
    return addrs


def _cn(name: x509.Name) -> str:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else name.rfc4514_string()


async def check_tcp_tls(
    report: TestReport, host: str, port: int, *, tls: bool, verify: bool, connect_timeout: float
) -> None:
    label = "Kết nối TLS" if tls else "Kết nối TCP"
    ctx: ssl.SSLContext | None = None
    if tls:
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ctx, server_hostname=host if tls else None),
            connect_timeout,
        )
    except ssl.SSLCertVerificationError as exc:
        report.add(
            TestStep(
                "tls",
                label,
                "fail",
                f"Chứng chỉ máy chủ không hợp lệ: {exc.verify_message}",
                "Nếu máy chủ dùng chứng chỉ tự ký, bật “Bỏ qua kiểm tra chứng chỉ SSL” "
                "hoặc cài CA nội bộ vào image.",
            )
        )
        return
    except TimeoutError:
        report.add(
            TestStep(
                "tls",
                label,
                "fail",
                f"Hết thời gian chờ kết nối tới {host}:{port}.",
                "Kiểm tra firewall giữa cluster và máy chủ, và cổng dịch vụ.",
            )
        )
        return
    except OSError as exc:
        report.add(
            TestStep(
                "tls",
                label,
                "fail",
                f"Không kết nối được {host}:{port}: {exc.strerror or exc}",
                "Kiểm tra máy chủ đang chạy, cổng đúng và firewall cho phép.",
            )
        )
        return
    try:
        if not tls:
            report.add(
                TestStep(
                    "tls",
                    label,
                    "warn",
                    f"Kết nối {host}:{port} không mã hoá.",
                    "Nên dùng HTTPS để bảo vệ thông tin đăng nhập.",
                )
            )
            return
        sslobj = writer.get_extra_info("ssl_object")
        der = sslobj.getpeercert(binary_form=True) if sslobj else None
        if not der:
            report.add(TestStep("tls", label, "ok", f"Đã kết nối {host}:{port}"))
            return
        cert = x509.load_der_x509_certificate(der)
        not_after = cert.not_valid_after_utc
        days = (not_after - datetime.now(UTC)).days
        detail = (
            f"{sslobj.version()} · CN={_cn(cert.subject)} · cấp bởi {_cn(cert.issuer)} · "
            f"hết hạn {not_after:%d/%m/%Y}"
        )
        report.info["tls_certificate_expires_at"] = not_after.isoformat()
        if days < 0:
            report.add(
                TestStep("tls", label, "fail" if verify else "warn", detail, "Chứng chỉ máy chủ đã hết hạn.")
            )
        elif not verify:
            report.add(
                TestStep(
                    "tls",
                    label,
                    "warn",
                    detail + " · không kiểm tra chứng chỉ",
                    "Đang bỏ qua kiểm tra chứng chỉ SSL; chỉ nên dùng cho chứng chỉ tự ký nội bộ.",
                )
            )
        elif days < 30:
            report.add(TestStep("tls", label, "warn", detail, f"Chứng chỉ máy chủ hết hạn sau {days} ngày."))
        else:
            report.add(TestStep("tls", label, "ok", detail))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass
        del reader


async def check_reachability(report: TestReport, url: str, *, verify: bool, connect_timeout: float) -> bool:
    """DNS then TCP/TLS. Returns False when a later HTTP step would be pointless."""
    host, port, tls = split_host_port(url)
    if not host:
        report.add(
            TestStep(
                "dns", "Địa chỉ máy chủ", "fail", "Địa chỉ không hợp lệ.", "Ví dụ: https://sdp.congty.local"
            )
        )
        return False
    if not await check_dns(report, host):
        return False
    await check_tcp_tls(report, host, port, tls=tls, verify=verify, connect_timeout=connect_timeout)
    return not report.failed()
