"""ManageEngine ServiceDesk Plus (API v3) – on-prem (authtoken) or cloud (Zoho OAuth)."""

from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from app.core.connectors import http
from app.core.connectors.base import (
    ConnectionContext,
    ConnectorType,
    PermissionCheck,
    Purpose,
    TestReport,
    TestStep,
)
from app.core.connectors.netcheck import check_reachability
from app.core.connectors.registry import register

ACCEPT_V3 = "application/vnd.manageengine.sdp.v3+json"
ZOHO_ACCOUNTS = {
    "us": "https://accounts.zoho.com",
    "eu": "https://accounts.zoho.eu",
    "in": "https://accounts.zoho.in",
    "au": "https://accounts.zoho.com.au",
    "jp": "https://accounts.zoho.jp",
    "ca": "https://accounts.zohocloud.ca",
    "uk": "https://accounts.zoho.uk",
    "cn": "https://accounts.zoho.com.cn",
    "sa": "https://accounts.zoho.sa",
}


class SdpConfig(BaseModel):
    edition: Literal["onprem", "cloud"] = Field(
        "onprem", title="Phiên bản", description="On-prem dùng authtoken; Cloud dùng Zoho OAuth."
    )
    verify_tls: bool = Field(
        True, title="Kiểm tra chứng chỉ SSL", description="Tắt nếu máy chủ dùng chứng chỉ tự ký."
    )
    data_center: Literal["us", "eu", "in", "au", "jp", "ca", "uk", "cn", "sa"] = Field(
        "us", title="Data center (Cloud)", description="Chỉ dùng cho bản Cloud."
    )


class SdpError(http.ConnectorHttpError):
    pass


class SdpClient:
    """Thin API v3 client shared by connection tests and module adapters."""

    def __init__(self, ctx: ConnectionContext) -> None:
        self.ctx = ctx
        self.config: SdpConfig = ctx.config  # type: ignore[assignment]
        self._client = http.make_client(
            ctx.base_url, verify=self.config.verify_tls, timeout=ctx.timeout, headers={"Accept": ACCEPT_V3}
        )
        self._access_token: str | None = None

    async def __aenter__(self) -> SdpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _auth_headers(self) -> dict[str, str]:
        if self.ctx.auth_type == "api_key":
            return {"authtoken": self.ctx.credential("api_key")}
        if self._access_token is None:
            await self.refresh_access_token()
        return {"Authorization": f"Zoho-oauthtoken {self._access_token}"}

    async def refresh_access_token(self) -> None:
        url = f"{ZOHO_ACCOUNTS[self.config.data_center]}/oauth/v2/token"
        params = {
            "refresh_token": self.ctx.credential("refresh_token"),
            "client_id": self.ctx.credential("client_id"),
            "client_secret": self.ctx.credential("client_secret"),
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=self.ctx.timeout) as client:
            resp = await http.request(client, "POST", url, params=params)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        token = data.get("access_token")
        if resp.status_code != 200 or not token:
            raise SdpError(
                f"Không lấy được access token Zoho: {data.get('error', resp.status_code)}",
                status=resp.status_code,
                hint="Kiểm tra client ID/secret, refresh token và data center.",
            )
        self._access_token = str(token)

    async def get(self, path: str, list_info: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {"input_data": json.dumps({"list_info": list_info})} if list_info else None
        resp = await http.request(
            self._client, "GET", path, params=params, headers=await self._auth_headers()
        )
        return self._parse(resp)

    async def list_all(
        self,
        path: str,
        collection: str,
        *,
        search_criteria: list[dict[str, Any]] | dict[str, Any] | None = None,
        sort_field: str | None = None,
        fields_required: list[str] | None = None,
        page_size: int = 100,
        max_rows: int = 20000,
    ) -> list[dict[str, Any]]:
        """Read every page of a v3 list endpoint (``list_info.has_more_rows``)."""
        rows: list[dict[str, Any]] = []
        start = 1
        while len(rows) < max_rows:
            info: dict[str, Any] = {"row_count": page_size, "start_index": start}
            if search_criteria:
                info["search_criteria"] = search_criteria
            if sort_field:
                info["sort_field"] = sort_field
                info["sort_order"] = "desc"
            if fields_required:
                info["fields_required"] = fields_required
            data = await self.get(path, info)
            page = data.get(collection) or []
            rows.extend(page)
            if not (data.get("list_info") or {}).get("has_more_rows") or not page:
                break
            start += len(page)
        return rows[:max_rows]

    async def write(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """API v3 write: the JSON body goes in the ``input_data`` form field. Not retried (avoid duplicates)."""
        resp = await http.request(
            self._client,
            method,
            path,
            max_attempts=1,
            headers=await self._auth_headers(),
            data={"input_data": json.dumps(payload, ensure_ascii=False)},
        )
        return self._parse(resp)

    # -- typed helpers used by workflows ------------------------------------------------
    async def get_request(self, request_id: str) -> dict[str, Any]:
        return (await self.get(f"/api/v3/requests/{request_id}")).get("request") or {}

    async def get_asset(self, asset_id: str) -> dict[str, Any]:
        return (await self.get(f"/api/v3/assets/{asset_id}")).get("asset") or {}

    async def find_users(self, field: str, value: str) -> list[dict[str, Any]]:
        return await self.list_all(
            "/api/v3/users",
            "users",
            search_criteria={"field": field, "condition": "is", "value": value},
            max_rows=20,
        )

    async def assets_of_user(self, user_id: str) -> list[dict[str, Any]]:
        return await self.list_all(
            "/api/v3/assets",
            "assets",
            search_criteria={"field": "user.id", "condition": "is", "value": user_id},
            max_rows=200,
        )

    async def update_asset(self, asset_id: str, state: str, user_id: str | None) -> dict[str, Any]:
        asset: dict[str, Any] = {"state": {"name": state}, "user": {"id": user_id} if user_id else None}
        return (await self.write("PUT", f"/api/v3/assets/{asset_id}", {"asset": asset})).get("asset") or {}

    async def add_note(self, request_id: str, html: str) -> dict[str, Any]:
        note = {
            "description": html,
            "show_to_requester": False,
            "mark_first_response": False,
            "add_to_linked_requests": False,
        }
        return await self.write("POST", f"/api/v3/requests/{request_id}/notes", {"note": note})

    async def set_request_status(self, request_id: str, status: str) -> dict[str, Any]:
        return await self.write(
            "PUT", f"/api/v3/requests/{request_id}", {"request": {"status": {"name": status}}}
        )

    async def post_file(self, path: str, field: str, filename: str, data: bytes, mime: str) -> dict[str, Any]:
        """Multipart upload (e.g. request attachments). Never retried on failure to avoid duplicates."""
        resp = await http.request(
            self._client,
            "POST",
            path,
            max_attempts=1,
            headers=await self._auth_headers(),
            files={field: (filename, data, mime)},
        )
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> dict[str, Any]:
        try:
            data: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise SdpError(
                f"Máy chủ trả về dữ liệu không phải JSON (HTTP {resp.status_code}).",
                status=resp.status_code,
                hint="Kiểm tra Base URL trỏ đúng tới ServiceDesk Plus (bản on-prem không có /app/...).",
            ) from exc
        status = data.get("response_status")
        if isinstance(status, list):
            status = status[0] if status else {}
        if resp.status_code in (401, 403) or (status and status.get("status") == "failed"):
            messages = (status or {}).get("messages") or []
            msg = (
                "; ".join(str(m.get("message", m)) for m in messages if isinstance(m, dict))
                or f"HTTP {resp.status_code}"
            )
            code = (status or {}).get("status_code")
            hint = ""
            if resp.status_code == 401 or code in (4001, 4002):
                hint = "API key không hợp lệ hoặc đã hết hạn. Tạo lại key cho technician trong SDP."
            elif resp.status_code == 403:
                hint = "Technician sở hữu API key chưa có quyền với module này trong SDP."
            raise SdpError(msg, status=resp.status_code, hint=hint)
        if resp.status_code >= 400:
            raise SdpError(f"HTTP {resp.status_code}", status=resp.status_code)
        return data


@register
class SdpConnector(ConnectorType):
    system_type = "sdp"
    display_name = "ServiceDesk Plus"
    description = "ITSM: request onboard/offboard, tài sản, worklog, đính kèm."
    icon = "ticket"
    base_url_hint = "https://sdp.congty.local:8080"
    config_model = SdpConfig
    auth_types = ("api_key", "oauth2_refresh_token")

    READ_PROBES = (
        ("Đọc request", "/api/v3/requests"),
        ("Đọc tài sản", "/api/v3/assets"),
        ("Đọc người dùng", "/api/v3/users"),
    )

    async def test_connection(self, ctx: ConnectionContext) -> TestReport:
        report = TestReport()
        config: SdpConfig = ctx.config  # type: ignore[assignment]
        if not await check_reachability(
            report, ctx.base_url, verify=config.verify_tls, connect_timeout=ctx.timeout
        ):
            return report
        async with SdpClient(ctx) as client:
            try:
                if ctx.auth_type == "oauth2_refresh_token":
                    await client.refresh_access_token()
                data = await client.get("/api/v3/requests", {"row_count": 1, "get_total_count": True})
            except http.ConnectorHttpError as exc:
                report.add(TestStep("auth", "Xác thực API", "fail", exc.message, exc.hint))
                return report
            total = (data.get("list_info") or {}).get("total_count")
            report.add(
                TestStep(
                    "auth",
                    "Xác thực API",
                    "ok",
                    "Đăng nhập API v3 thành công" + (f" · {total} request" if total is not None else ""),
                )
            )
            for label, path in self.READ_PROBES:
                try:
                    probe = await client.get(path, {"row_count": 1, "get_total_count": True})
                    count = (probe.get("list_info") or {}).get("total_count")
                    report.permissions.append(
                        PermissionCheck(label, True, f"{count} bản ghi" if count is not None else "")
                    )
                except http.ConnectorHttpError as exc:
                    report.permissions.append(PermissionCheck(label, False, exc.message))
        if ctx.purpose == Purpose.WRITE:
            report.permissions.append(
                PermissionCheck(
                    "Ghi worklog, cập nhật tài sản, đính kèm",
                    None,
                    "Không kiểm tra được mà không ghi dữ liệu; sẽ xác nhận ở lần thực thi đầu tiên.",
                )
            )
        if any(p.granted is False for p in report.permissions):
            report.add(
                TestStep(
                    "permissions",
                    "Quyền truy cập",
                    "warn",
                    "Thiếu một số quyền đọc.",
                    "Cấp quyền cho technician sở hữu API key trong SDP (Admin → Roles).",
                )
            )
        else:
            report.add(TestStep("permissions", "Quyền truy cập", "ok", "Đủ quyền đọc cần thiết."))
        return report
