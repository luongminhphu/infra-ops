"""Microsoft Graph (app-only, OAuth2 client credentials): licences, usage location, sign-in sessions.

Certificate (client assertion) auth is not offered: it needs JWT signing; add it when required.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

from pydantic import BaseModel, Field

from app.core.connectors import http
from app.core.connectors.base import (
    ConnectionContext,
    ConnectorType,
    PermissionCheck,
    TestReport,
    TestStep,
)
from app.core.connectors.netcheck import check_reachability
from app.core.connectors.registry import register

SCOPE = "https://graph.microsoft.com/.default"
USER_SELECT = "id,userPrincipalName,displayName,accountEnabled,mail,usageLocation,assignedLicenses,licenseAssignmentStates,employeeId,provisionedPlans"
_GUID = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_EMP = re.compile(r"^\d{1,20}$")

HINTS = {
    400: "Yêu cầu không hợp lệ; kiểm tra dữ liệu gửi lên Microsoft Graph.",
    401: "Token bị từ chối; kiểm tra tenant, client ID và client secret.",
    403: "Ứng dụng Entra chưa được cấp quyền (API permissions) và admin consent cho thao tác này.",
    404: "Không tìm thấy đối tượng trên Microsoft Graph.",
    429: "Microsoft Graph đang giới hạn tốc độ; thử lại sau.",
}


class MsGraphConfig(BaseModel):
    tenant_id: str = Field(title="Tenant ID", pattern=_GUID, description="Directory (tenant) ID dạng GUID.")
    authority_host: str = Field(
        "https://login.microsoftonline.com",
        title="Máy chủ xác thực",
        pattern=r"^https://\S+$",
    )
    timeout_seconds: float = Field(20.0, title="Timeout (giây)", ge=5, le=120)


class MsGraphError(http.ConnectorHttpError):
    pass


def token_roles(token: str) -> list[str] | None:
    """Read the ``roles`` claim without verifying the token (display only). None when unreadable."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        roles = claims.get("roles", [])
        return [str(r) for r in roles] if isinstance(roles, list) else None
    except (IndexError, ValueError):
        return None


class GraphClient:
    def __init__(self, ctx: ConnectionContext) -> None:
        self.ctx = ctx
        self.config: MsGraphConfig = ctx.config  # type: ignore[assignment]
        for url in (ctx.base_url, self.config.authority_host):
            if not url.lower().startswith("https://"):
                raise MsGraphError(
                    "Microsoft Graph chỉ hỗ trợ HTTPS.", hint="Dùng địa chỉ bắt đầu bằng https://"
                )
        timeout = min(ctx.timeout, self.config.timeout_seconds)
        self._client = http.make_client(ctx.base_url, verify=True, timeout=timeout)
        self._auth = http.make_client(self.config.authority_host, verify=True, timeout=timeout)
        self._token: str | None = None
        self._expires_at = 0.0

    async def __aenter__(self) -> GraphClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()
        await self._auth.aclose()

    async def token(self) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        resp = await http.request(
            self._auth,
            "POST",
            f"/{self.config.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.ctx.credential("client_id"),
                "client_secret": self.ctx.credential("client_secret"),
                "scope": SCOPE,
            },
        )
        data = self._json(resp)
        if resp.status_code != 200 or not data.get("access_token"):
            # Only the error code: descriptions can echo request details.
            raise MsGraphError(
                f"Lấy token Microsoft Entra thất bại: {data.get('error') or resp.status_code}",
                status=resp.status_code,
                hint="Kiểm tra Tenant ID, Client ID, Client secret (chưa hết hạn) của ứng dụng Entra.",
            )
        self._token = str(data["access_token"])
        try:
            lifetime = float(data.get("expires_in") or 3600)
        except (TypeError, ValueError):
            lifetime = 0.0
        self._expires_at = time.monotonic() + max(lifetime - 60, 0)
        return self._token

    async def _call(
        self,
        method: str,
        path: str,
        *,
        ok_404: bool = False,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        for refreshed in (False, True):
            token = await self.token()
            resp = await http.request(
                self._client,
                method,
                path,
                max_attempts=3 if method == "GET" else 1,  # writes are never retried blindly
                headers={"Authorization": f"Bearer {token}", **(headers or {})},
                **kwargs,
            )
            if resp.status_code != 401 or refreshed:
                break
            self._token = None  # 401 = not executed, so retrying even a write once is safe
        if ok_404 and resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            err = self._json(resp).get("error")
            code = err.get("code") if isinstance(err, dict) else None
            raise MsGraphError(
                f"Microsoft Graph từ chối: {code or resp.status_code}",
                status=resp.status_code,
                hint=HINTS.get(resp.status_code, ""),
            )
        return self._json(resp) if resp.content else {}

    async def find_user_by_employee_id(self, emp: str) -> list[dict[str, Any]]:
        if not _EMP.match(emp):
            raise ValueError("Mã nhân viên chỉ gồm chữ số.")
        data = await self._call(
            "GET",
            "/users",
            params={
                "$filter": f"employeeId eq '{emp.replace(chr(39), chr(39) * 2)}'",
                "$select": USER_SELECT,
                "$count": "true",
            },
            headers={"ConsistencyLevel": "eventual"},
        )
        return (data or {}).get("value") or []

    async def find_user_by_upn(self, upn: str) -> dict[str, Any] | None:
        data = await self._call(
            "GET",
            "/users",
            params={
                "$filter": f"userPrincipalName eq '{upn.replace(chr(39), chr(39) * 2)}'",
                "$select": USER_SELECT,
                "$count": "true",
            },
            headers={"ConsistencyLevel": "eventual"},
        )
        found = (data or {}).get("value") or []
        return found[0] if found else None

    async def address_taken(self, value: str) -> bool:
        """True when any user (live or deleted) already uses this UPN, mail or smtp proxy address."""
        v = value.replace("'", "''")
        live = f"userPrincipalName eq '{v}' or mail eq '{v}' or proxyAddresses/any(p:p eq 'smtp:{v}')"
        data = await self._call(
            "GET",
            "/users",
            params={"$filter": live, "$select": "id", "$count": "true", "$top": "1"},
            headers={"ConsistencyLevel": "eventual"},
        )
        if (data or {}).get("value"):
            return True
        deleted = await self._call(
            "GET",
            "/directory/deletedItems/microsoft.graph.user",
            params={"$filter": f"userPrincipalName eq '{v}' or mail eq '{v}'", "$select": "id", "$top": "1"},
        )
        return bool((deleted or {}).get("value"))

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        return await self._call(
            "GET", f"/users/{self._uid(user_id)}", ok_404=True, params={"$select": USER_SELECT}
        )

    async def subscribed_skus(self) -> list[dict[str, Any]]:
        data = await self._call("GET", "/subscribedSkus")
        return [
            {
                "skuId": s.get("skuId"),
                "skuPartNumber": s.get("skuPartNumber"),
                "enabled": (s.get("prepaidUnits") or {}).get("enabled", 0),
                "consumed": s.get("consumedUnits", 0),
            }
            for s in (data or {}).get("value") or []
        ]

    async def free_units(self, sku_id: str) -> int | None:
        for s in await self.subscribed_skus():
            if str(s["skuId"]).lower() == sku_id.lower():
                return int(s["enabled"]) - int(s["consumed"])
        return None

    async def set_usage_location(self, user_id: str, code: str) -> None:
        await self._call("PATCH", f"/users/{self._uid(user_id)}", json={"usageLocation": code})

    async def assign_licenses(self, user_id: str, add_sku_ids: list[str]) -> None:
        body = {"addLicenses": [{"skuId": s, "disabledPlans": []} for s in add_sku_ids], "removeLicenses": []}
        await self._call("POST", f"/users/{self._uid(user_id)}/assignLicense", json=body)

    async def swap_licenses(self, user_id: str, add_sku_ids: list[str], remove_sku_ids: list[str]) -> None:
        """Add and remove SKUs in one assignLicense call (atomic on the Graph side)."""
        if not add_sku_ids and not remove_sku_ids:
            return
        for s in (*add_sku_ids, *remove_sku_ids):
            if not re.match(_GUID, s):
                raise ValueError("Mã SKU Microsoft Graph phải là GUID.")
        body = {
            "addLicenses": [{"skuId": s, "disabledPlans": []} for s in add_sku_ids],
            "removeLicenses": list(remove_sku_ids),
        }
        await self._call("POST", f"/users/{self._uid(user_id)}/assignLicense", json=body)

    async def revoke_sessions(self, user_id: str) -> None:
        await self._call("POST", f"/users/{self._uid(user_id)}/revokeSignInSessions")

    async def deleted_user(self, user_id: str) -> dict[str, Any] | None:
        return await self._call("GET", f"/directory/deletedItems/{self._uid(user_id)}", ok_404=True)

    @staticmethod
    def _uid(user_id: str) -> str:
        if not re.match(_GUID, user_id):
            raise ValueError("Mã người dùng Microsoft Graph phải là GUID.")
        return user_id

    @staticmethod
    def _json(resp: Any) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


@register
class MsGraphConnector(ConnectorType):
    system_type = "msgraph"
    display_name = "Microsoft Graph (Entra ID / Microsoft 365)"
    description = "Giấy phép Microsoft 365, vị trí sử dụng và phiên đăng nhập (ứng dụng Entra, app-only)."
    icon = "cloud"
    base_url_hint = "https://graph.microsoft.com/v1.0"
    config_model = MsGraphConfig
    auth_types = ("oauth2_client_credentials",)

    async def test_connection(self, ctx: ConnectionContext) -> TestReport:
        report = TestReport()
        config: MsGraphConfig = ctx.config  # type: ignore[assignment]
        for url in (config.authority_host, ctx.base_url):
            if not await check_reachability(report, url, verify=True, connect_timeout=ctx.timeout):
                return report
        async with GraphClient(ctx) as client:
            try:
                token = await client.token()
            except http.ConnectorHttpError as exc:
                report.add(TestStep("auth", "Lấy token Microsoft Entra", "fail", exc.message, exc.hint))
                return report
            report.add(TestStep("auth", "Lấy token Microsoft Entra", "ok", "Đã nhận access token."))
            roles = token_roles(token)
            for label, path, params in (
                ("Đọc giấy phép (subscribedSkus)", "/subscribedSkus", None),
                ("Đọc người dùng (User.Read.All)", "/users", {"$top": "1"}),
            ):
                try:
                    await client._call("GET", path, params=params)
                    report.permissions.append(PermissionCheck(label, True))
                except http.ConnectorHttpError as exc:
                    report.permissions.append(PermissionCheck(label, False, exc.message))
        for label, any_of in (
            ("Gán giấy phép / đặt vị trí sử dụng", ("LicenseAssignment.ReadWrite.All", "User.ReadWrite.All")),
            ("Thu hồi phiên đăng nhập", ("User.RevokeSessions.All", "User.ReadWrite.All")),
        ):
            if roles is None:
                report.permissions.append(PermissionCheck(label, None, "Không đọc được quyền từ token."))
            else:
                granted = any(r in roles for r in any_of)
                report.permissions.append(
                    PermissionCheck(label, granted, "Cần một trong: " + ", ".join(any_of))
                )
        if any(p.granted is False for p in report.permissions[:2]):
            report.add(
                TestStep("permissions", "Quyền truy cập", "warn", "Thiếu một số quyền đọc.", HINTS[403])
            )
        else:
            report.add(TestStep("permissions", "Quyền truy cập", "ok", "Đủ quyền đọc cần thiết."))
        return report
