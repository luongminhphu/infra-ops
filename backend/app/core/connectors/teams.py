"""Microsoft Teams incoming webhook: team-wide totals of the "Việc tiếp theo" digest.

The webhook URL is the credential (anyone holding it can post), so it is stored as a secret and never
logged, audited or put into an error message. The profile's ``base_url`` is a placeholder and not used.
``test_connection`` only resolves the host and completes a TLS handshake: it never POSTs.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from app.core.connectors.base import (
    AUTH_TYPES,
    AuthField,
    AuthTypeSpec,
    ConnectionContext,
    ConnectorType,
    TestReport,
)
from app.core.connectors.netcheck import check_reachability
from app.core.connectors.registry import register

AUTH_TYPES.setdefault(
    "webhook_url",
    AuthTypeSpec(
        "webhook_url",
        "Webhook của kênh Teams",
        (
            AuthField(
                "webhook_url", "Webhook URL", secret=True, help="URL dán từ Teams (Workflows / Webhook)."
            ),
        ),
    ),
)


class TeamsConfig(BaseModel):
    pass


class TeamsError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def post_message(ctx: ConnectionContext, text: str) -> None:
    """POST one Adaptive Card with ``text``. Single attempt (no blind retry of a write); stable error codes."""
    url = ctx.credential("webhook_url")
    if not url.lower().startswith("https://"):
        raise TeamsError("teams_invalid_webhook")
    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "version": "1.4",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "body": [{"type": "TextBlock", "text": text, "wrap": True}],
    }
    payload = {
        "type": "message",
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
    }
    try:
        async with httpx.AsyncClient(timeout=ctx.timeout, follow_redirects=False) as client:
            resp = await client.post(url, json=payload)
    except httpx.InvalidURL:
        raise TeamsError("teams_invalid_webhook") from None
    except httpx.HTTPError:
        raise TeamsError("teams_unreachable") from None
    if resp.status_code >= 300:
        raise TeamsError(f"teams_http_{resp.status_code}")


@register
class TeamsConnector(ConnectorType):
    system_type = "teams"
    display_name = "Microsoft Teams (webhook)"
    description = "Đăng số liệu tổng hợp “Việc tiếp theo” của cả nhóm lên một kênh."
    icon = "message-square"
    base_url_hint = "Không dùng; nhập https://teams.microsoft.com. URL webhook nhập ở phần credential."
    config_model = TeamsConfig
    auth_types = ("webhook_url",)

    async def test_connection(self, ctx: ConnectionContext) -> TestReport:
        report = TestReport()
        url = ctx.credential("webhook_url")
        if not url.lower().startswith("https://"):
            return TestReport.fail("webhook", "Webhook URL", "Webhook phải bắt đầu bằng https://.")
        await check_reachability(report, url, verify=True, connect_timeout=ctx.timeout)
        return report
