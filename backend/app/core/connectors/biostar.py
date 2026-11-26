"""Suprema BioStar 2 REST API (server managing BioStation 3 devices)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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

SESSION_HEADER = "bs-session-id"


class BioStarConfig(BaseModel):
    verify_tls: bool = Field(
        True, title="Kiểm tra chứng chỉ SSL", description="BioStar thường dùng chứng chỉ tự ký; tắt nếu cần."
    )


class BioStarError(http.ConnectorHttpError):
    pass


class BioStarClient:
    """Login with ``/api/login``, keep ``bs-session-id`` and re-login once when the session expires."""

    def __init__(self, ctx: ConnectionContext) -> None:
        self.ctx = ctx
        config: BioStarConfig = ctx.config  # type: ignore[assignment]
        self._client = http.make_client(ctx.base_url, verify=config.verify_tls, timeout=ctx.timeout)
        self.session_id: str | None = None
        self.login_user: dict[str, Any] = {}

    async def __aenter__(self) -> BioStarClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.session_id:
            try:
                await self._client.post("/api/logout", headers={SESSION_HEADER: self.session_id})
            except httpx.HTTPError:
                pass
        await self._client.aclose()

    async def login(self) -> dict[str, Any]:
        body = {
            "User": {"login_id": self.ctx.credential("username"), "password": self.ctx.credential("password")}
        }
        resp = await http.request(self._client, "POST", "/api/login", json=body)
        data = self._json(resp)
        session_id = resp.headers.get(SESSION_HEADER)
        if resp.status_code != 200 or not session_id:
            message = (data.get("Response") or {}).get("message") or f"HTTP {resp.status_code}"
            raise BioStarError(
                f"Đăng nhập BioStar thất bại: {message}",
                status=resp.status_code,
                hint="Kiểm tra tài khoản/mật khẩu BioStar và tài khoản chưa bị khoá.",
            )
        self.session_id = session_id
        self.login_user = data.get("User") or {}
        return self.login_user

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._read("GET", path, params=params)

    async def _read(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> dict[str, Any]:
        """A call that changes nothing (GET, or a POST search): retried, with one re-login on 401."""
        if not self.session_id:
            await self.login()
        for _ in range(2):
            resp = await http.request(
                self._client,
                method,
                path,
                params=params,
                json=json,
                headers={SESSION_HEADER: self.session_id or ""},
            )
            if resp.status_code != 401:
                break
            await self.login()
        data = self._json(resp)
        if resp.status_code >= 400:
            message = (data.get("Response") or {}).get("message") or f"HTTP {resp.status_code}"
            raise BioStarError(
                message,
                status=resp.status_code,
                hint="Tài khoản BioStar cần quyền tương ứng (Operator/Administrator)."
                if resp.status_code == 403
                else "",
            )
        return data

    async def send(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Write call with one re-login on an expired session. Never retried otherwise."""
        if not self.session_id:
            await self.login()
        for attempt in range(2):
            resp = await http.request(
                self._client,
                method,
                path,
                max_attempts=1,
                json=json_body,
                params=params,
                headers={SESSION_HEADER: self.session_id or ""},
            )
            if resp.status_code == 401 and attempt == 0:
                await self.login()
                continue
            data = self._json(resp) if resp.content else {}
            code = str((data.get("Response") or {}).get("code", "0"))
            if resp.status_code >= 400 or code not in ("0", ""):
                message = (data.get("Response") or {}).get("message") or f"HTTP {resp.status_code}"
                raise BioStarError(
                    f"BioStar từ chối: {message}",
                    status=resp.status_code,
                    hint="Tài khoản Write cần quyền quản lý User trong BioStar."
                    if resp.status_code == 403
                    else "",
                )
            return resp.status_code, data
        raise AssertionError("unreachable")

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        if not self.session_id:
            await self.login()
        resp = await http.request(
            self._client, "GET", f"/api/users/{user_id}", headers={SESSION_HEADER: self.session_id or ""}
        )
        if resp.status_code == 401:
            await self.login()
            resp = await http.request(
                self._client, "GET", f"/api/users/{user_id}", headers={SESSION_HEADER: self.session_id or ""}
            )
        if resp.status_code == 404:
            return None
        data = self._json(resp)
        if resp.status_code >= 400:
            raise BioStarError(
                (data.get("Response") or {}).get("message") or f"HTTP {resp.status_code}",
                status=resp.status_code,
            )
        user = data.get("User")
        return user if isinstance(user, dict) and user.get("user_id") else None

    async def create_user(self, user: dict[str, Any]) -> dict[str, Any]:
        return (await self.send("POST", "/api/users", json_body={"User": user}))[1]

    async def update_user(self, user_id: str, user: dict[str, Any]) -> dict[str, Any]:
        return (await self.send("PUT", f"/api/users/{user_id}", json_body={"User": user}))[1]

    async def delete_user(self, user_id: str) -> dict[str, Any]:
        return (await self.send("DELETE", "/api/users", params={"id": user_id}))[1]

    async def user_groups(self) -> list[dict[str, Any]]:
        return (await self.get("/api/user_groups")).get("UserGroupCollection", {}).get("rows") or []

    async def access_groups(self) -> list[dict[str, Any]]:
        return (await self.get("/api/access_groups")).get("AccessGroupCollection", {}).get("rows") or []

    async def list_users(self, page_size: int = 500, max_rows: int = 50000) -> list[dict[str, Any]]:
        """All BioStar users, paging with limit/offset until ``UserCollection.total`` is reached."""
        rows: list[dict[str, Any]] = []
        offset = 0
        while len(rows) < max_rows:
            data = await self.get("/api/users", {"limit": page_size, "offset": offset})
            coll = data.get("UserCollection") or {}
            page = coll.get("rows") or []
            rows.extend(page)
            total = int(coll.get("total") or 0)
            offset += len(page)
            if not page or offset >= total:
                break
        return rows

    async def search_events(
        self,
        user_ids: list[str],
        start: datetime,
        end: datetime,
        page_size: int = 1000,
        max_rows: int = 200_000,
    ) -> list[dict[str, Any]]:
        """Access-log events of these users between start and end (timezone-aware), oldest first."""
        rows: list[dict[str, Any]] = []
        conditions = [
            {"column": "datetime", "operator": 3, "values": [_iso(start), _iso(end)]},  # BETWEEN
            {"column": "user_id.user_id", "operator": 2, "values": user_ids},  # IN
        ]
        while len(rows) < max_rows:
            query = {
                "limit": page_size,
                "offset": len(rows),
                "conditions": conditions,
                "orders": [{"column": "datetime", "descending": False}],
            }
            data = await self._read("POST", "/api/events/search", json={"Query": query})
            page = (data.get("EventCollection") or {}).get("rows") or []
            rows.extend(page)
            if len(page) < page_size:
                break
        return rows

    @staticmethod
    def _json(resp: httpx.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError as exc:
            raise BioStarError(
                f"Máy chủ không trả về JSON (HTTP {resp.status_code}).",
                status=resp.status_code,
                hint="Kiểm tra Base URL trỏ tới BioStar 2 (vd. https://biostar.congty.local).",
            ) from exc
        return data if isinstance(data, dict) else {}


def _iso(d: datetime) -> str:
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _total(data: dict[str, Any], collection: str) -> str | None:
    value = (data.get(collection) or {}).get("total")
    return str(value) if value is not None else None


@register
class BioStarConnector(ConnectorType):
    system_type = "biostar"
    display_name = "Suprema BioStar 2"
    description = "Kiểm soát ra vào: người dùng, nhóm người dùng, nhóm quyền ra vào (BioStation 3)."
    icon = "fingerprint"
    base_url_hint = "https://biostar.congty.local"
    config_model = BioStarConfig
    auth_types = ("username_password",)

    READ_PROBES = (
        ("Đọc người dùng", "/api/users", {"limit": 1, "offset": 0}, "UserCollection"),
        ("Đọc nhóm người dùng", "/api/user_groups", None, "UserGroupCollection"),
        ("Đọc nhóm quyền ra vào", "/api/access_groups", None, "AccessGroupCollection"),
        ("Đọc thiết bị", "/api/devices", None, "DeviceCollection"),
    )

    async def test_connection(self, ctx: ConnectionContext) -> TestReport:
        report = TestReport()
        config: BioStarConfig = ctx.config  # type: ignore[assignment]
        if not await check_reachability(
            report, ctx.base_url, verify=config.verify_tls, connect_timeout=ctx.timeout
        ):
            return report
        async with BioStarClient(ctx) as client:
            try:
                user = await client.login()
            except http.ConnectorHttpError as exc:
                report.add(TestStep("auth", "Đăng nhập BioStar", "fail", exc.message, exc.hint))
                return report
            role = (user.get("permission") or {}).get("name") or (user.get("user_group_id") or {}).get("name")
            report.info["login_user"] = user.get("name") or user.get("login_id")
            if role:
                report.info["role"] = role
            report.add(
                TestStep(
                    "auth", "Đăng nhập BioStar", "ok", f"Phiên làm việc đã tạo · quyền: {role or 'không rõ'}"
                )
            )
            for label, path, params, collection in self.READ_PROBES:
                try:
                    data = await client.get(path, params)
                    total = _total(data, collection)
                    report.permissions.append(
                        PermissionCheck(label, True, f"{total} bản ghi" if total else "")
                    )
                except http.ConnectorHttpError as exc:
                    report.permissions.append(PermissionCheck(label, False, exc.message))
        if ctx.purpose == Purpose.WRITE:
            report.permissions.append(
                PermissionCheck(
                    "Tạo/sửa/vô hiệu hoá người dùng",
                    None,
                    "Không kiểm tra được mà không ghi dữ liệu; tài khoản cần quyền Administrator "
                    "hoặc Operator có quyền quản lý User.",
                )
            )
        if any(p.granted is False for p in report.permissions):
            report.add(
                TestStep(
                    "permissions",
                    "Quyền truy cập",
                    "warn",
                    "Thiếu một số quyền đọc.",
                    "Gán quyền phù hợp cho tài khoản trong BioStar (Settings → Account).",
                )
            )
        else:
            report.add(TestStep("permissions", "Quyền truy cập", "ok", "Đủ quyền đọc cần thiết."))
        return report
