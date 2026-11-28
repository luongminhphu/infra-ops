"""Active Directory over LDAPS (ldap3). Synchronous library: every call runs in a worker thread."""

from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
import ssl
import string
from typing import Any

from ldap3 import (
    BASE,
    MODIFY_ADD,
    MODIFY_DELETE,
    MODIFY_REPLACE,
    NONE,
    SIMPLE,
    SUBTREE,
    Connection,
    Server,
    Tls,
)
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn, parse_dn, safe_rdn
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

_EMP = re.compile(r"^\d{1,20}$")
USER_ATTRS = [
    "sAMAccountName",
    "userPrincipalName",
    "displayName",
    "mail",
    "userAccountControl",
    "adminCount",
    "memberOf",
    "employeeID",
    "title",
    "department",
    "manager",
]
UAC_DISABLED = 0x2
NO_SUCH_OBJECT = 32
# ldap3/AD result codes: 20 attributeOrValueExists / 68 entryAlreadyExists (member already there),
# 16 noSuchAttribute / 53 unwillingToPerform (member not in group): both make add/remove idempotent.
ADD_MEMBER_OK = (0, 20, 68)
REMOVE_MEMBER_OK = (0, 16, 53)


class AdConfig(BaseModel):
    base_dn: str = Field(title="Base DN", description="Ví dụ: DC=congty,DC=local", min_length=3)
    ca_bundle_pem: str = Field("", title="CA nội bộ (PEM)", json_schema_extra={"multiline": True})
    timeout_seconds: int = Field(30, title="Timeout (giây)", ge=5, le=300)


class AdError(ConnectorHttpError):
    pass


def build_tls(ca_bundle_pem: str) -> Tls:
    return Tls(validate=ssl.CERT_REQUIRED, ca_certs_data=ca_bundle_pem.strip() or None)


def _rdns(dn: str) -> list[tuple[str, str]] | None:
    try:
        flat = re.sub(r"(?<!\\)\s*,\s*", ",", re.sub(r"\s*=\s*", "=", dn.strip()))  # parse_dn rejects padding
        return [(a.lower(), v.lower()) for a, v, _ in parse_dn(flat)]
    except LDAPException:
        return None


def inside(dn: str, base_ou: str) -> bool:
    """True when dn equals base_ou or sits below it (compared RDN by RDN, so escaped commas cannot fake a match)."""
    d, b = _rdns(dn), _rdns(base_ou)
    return bool(d and b and len(d) >= len(b) and d[len(d) - len(b) :] == b)


def random_password(length: int = 28) -> str:
    sets = (string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%^&*-_=+")
    chars = [secrets.choice(s) for s in sets]
    chars += [secrets.choice("".join(sets)) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


INT_ATTRS = {"userAccountControl", "adminCount"}
MULTI = {"memberOf", "allowedChildClassesEffective", "objectClass"}


def _flat(entry: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"dn": entry["dn"]}
    for k, v in (entry.get("attributes") or {}).items():
        as_list = v if isinstance(v, list) else ([] if v in (None, "") else [v])
        out[k] = as_list if k in MULTI else (as_list[0] if as_list else None)
        if k in INT_ATTRS and out[k] is not None:
            out[k] = int(out[k])
    return out


class AdClient:
    def __init__(self, ctx: ConnectionContext) -> None:
        self.ctx = ctx
        self.cfg: AdConfig = ctx.config  # type: ignore[assignment]
        self._conn: Any = None

    def _open(self) -> None:
        host, port, tls = split_host_port(self.ctx.base_url, 636)
        if not tls:
            raise AdError("Chỉ hỗ trợ LDAPS.", hint="Dùng địa chỉ dạng ldaps://dc.congty.local:636")
        user = self.ctx.credential("username")
        if "\\" in user:
            raise AdError("Dùng UPN (vd svc@congty.vn) cho tài khoản dịch vụ AD – không hỗ trợ DOMAIN\\user.")
        server = Server(
            host,
            port=port,
            use_ssl=True,
            get_info=NONE,
            tls=build_tls(self.cfg.ca_bundle_pem),
            connect_timeout=min(self.ctx.timeout, self.cfg.timeout_seconds),
        )
        self._conn = Connection(
            server,
            user=user,
            password=self.ctx.credential("password"),
            authentication=SIMPLE,
            receive_timeout=self.cfg.timeout_seconds,
            raise_exceptions=False,
        )
        if not self._conn.bind():
            res = self._conn.result or {}
            text = str(res.get("message") or "")
            reason = next((v for k, v in _BIND_REASONS.items() if f"data {k}" in text), "")
            conn, self._conn = self._conn, None
            with contextlib.suppress(Exception):  # already failing; unbind of a failed bind may raise
                conn.unbind()
            raise AdError(
                f"Bind LDAP thất bại (mã {res.get('result')}, {res.get('description') or 'unknown'})"
                + (f": {reason}." if reason else "."),
                hint="Kiểm tra tài khoản, mật khẩu và chứng chỉ LDAPS.",
            )

    async def __aenter__(self) -> AdClient:
        try:
            await asyncio.to_thread(self._open)
        except AdError:
            raise
        except Exception as exc:
            raise AdError(f"Không kết nối được LDAPS ({type(exc).__name__}).", hint=_TLS_HINT) from None
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.unbind)
            self._conn = None

    # --- sync primitives (run in threads) ---
    def _result_ok(self, allow: tuple[int, ...] = (0,)) -> int:
        code = int((self._conn.result or {}).get("result", -1))
        if code not in allow:
            desc = (self._conn.result or {}).get("description", "unknown")
            raise AdError(
                f"Active Directory từ chối thao tác: {desc}", hint="Kiểm tra quyền của tài khoản dịch vụ."
            )
        return code

    def _search(
        self, base: str, flt: str, attrs: list[str], *, scope: str = SUBTREE, limit: int = 50
    ) -> list[dict[str, Any]]:
        self._conn.search(base, flt, search_scope=scope, attributes=attrs, size_limit=limit)
        # 4 = sizeLimitExceeded: we only ever want the first few rows.
        if self._result_ok((0, 4, NO_SUCH_OBJECT)) == NO_SUCH_OBJECT:
            return []
        return [_flat(e) for e in (self._conn.response or []) if e.get("type") == "searchResEntry"]

    def _do(self, op: str, *args: Any, **kwargs: Any) -> None:
        getattr(self._conn, op)(*args, **kwargs)
        self._result_ok()

    async def _t(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except AdError:
            raise
        except Exception as exc:
            raise AdError(f"Lỗi LDAP ({type(exc).__name__}).", hint=_TLS_HINT) from None

    # --- queries ---
    async def find_users(self, employee_id: str) -> list[dict[str, Any]]:
        if not _EMP.match(employee_id):
            raise ValueError("Mã nhân viên chỉ gồm chữ số.")
        flt = f"(&(objectCategory=person)(objectClass=user)(employeeID={escape_filter_chars(employee_id)}))"
        return await self._t(self._search, self.cfg.base_dn, flt, USER_ATTRS)

    async def exists_any(self, sam: str | None, upn: str | None, mail: str | None) -> list[str]:
        parts = [f"(sAMAccountName={escape_filter_chars(sam)})"] if sam else []
        if upn:
            parts.append(f"(userPrincipalName={escape_filter_chars(upn)})")
        if mail:
            m = escape_filter_chars(mail)
            parts += [f"(mail={m})", f"(proxyAddresses=smtp:{m})", f"(proxyAddresses=SMTP:{m})"]
        if not parts:
            return []
        rows = await self._t(
            self._search, self.cfg.base_dn, f"(|{''.join(parts)})", ["distinguishedName"], limit=5
        )
        return [r["dn"] for r in rows]

    async def get(self, dn: str) -> dict[str, Any] | None:
        rows = await self._t(self._search, dn, "(objectClass=*)", USER_ATTRS, scope=BASE, limit=1)
        return rows[0] if rows else None

    async def get_group(self, dn: str) -> dict[str, Any] | None:
        rows = await self._t(
            self._search, dn, "(objectClass=*)", ["objectClass", "adminCount", "cn"], scope=BASE, limit=1
        )
        if not rows:
            return None
        g = rows[0]
        return {**g, "adminCount": g.get("adminCount") or 0}

    # --- writes ---
    async def add_user(self, ou_dn: str, cn: str, attrs: dict[str, Any]) -> str:
        dn = f"CN={escape_rdn(cn)},{ou_dn}"
        await self._t(self._do, "add", dn, "user", attrs)
        return dn

    async def set_password_random(self, dn: str) -> None:
        pwd = f'"{random_password()}"'.encode("utf-16-le")  # never returned or logged
        await self._t(self._do, "modify", dn, {"unicodePwd": [(MODIFY_REPLACE, [pwd])]})

    async def modify(self, dn: str, changes: dict[str, Any]) -> None:
        ops = {
            k: [(MODIFY_REPLACE, [])] if v is None else [(MODIFY_REPLACE, [v])] for k, v in changes.items()
        }
        await self._t(self._do, "modify", dn, ops)

    def _member_op(self, op: str, allow: tuple[int, ...], group_dn: str, user_dn: str) -> None:
        # MODIFY_ADD / MODIFY_DELETE of a single value only; never REPLACE (it would drop other members).
        self._conn.modify(group_dn, {"member": [(op, [user_dn])]})
        self._result_ok(allow)

    async def add_member(self, group_dn: str, user_dn: str) -> None:
        await self._t(self._member_op, MODIFY_ADD, ADD_MEMBER_OK, group_dn, user_dn)

    async def remove_member(self, group_dn: str, user_dn: str) -> None:
        await self._t(self._member_op, MODIFY_DELETE, REMOVE_MEMBER_OK, group_dn, user_dn)

    async def _set_disabled(self, dn: str, disabled: bool) -> None:
        entry = await self.get(dn)
        if entry is None:
            raise AdError("Không tìm thấy tài khoản trong Active Directory.")
        current = int(entry.get("userAccountControl") or 0)
        new = current | UAC_DISABLED if disabled else current & ~UAC_DISABLED
        if new != current:
            await self.modify(dn, {"userAccountControl": new})

    async def enable(self, dn: str) -> None:
        await self._set_disabled(dn, False)

    async def disable(self, dn: str) -> None:
        await self._set_disabled(dn, True)

    async def move(self, dn: str, new_ou_dn: str, new_rdn: str | None = None) -> None:
        """Move under new_ou_dn; new_rdn (e.g. "CN=Name (123)", already escaped) renames on the way."""
        rdn = new_rdn or safe_rdn(dn, decompose=False)[0]
        await self._t(self._do, "modify_dn", dn, rdn, new_superior=new_ou_dn)


_BIND_REASONS = {"52e": "sai mật khẩu", "775": "tài khoản bị khoá"}
_TLS_HINT = "Kiểm tra DC, cổng 636, CA nội bộ và tài khoản dịch vụ."


@register
class AdConnector(ConnectorType):
    system_type = "active_directory"
    display_name = "Active Directory (LDAPS)"
    description = "Tạo, vô hiệu hoá và chuyển OU tài khoản người dùng trong Active Directory nội bộ."
    icon = "users"
    base_url_hint = "ldaps://dc.congty.local:636"
    config_model = AdConfig
    auth_types = ("username_password",)

    async def test_connection(self, ctx: ConnectionContext) -> TestReport:
        report = TestReport()
        config: AdConfig = ctx.config  # type: ignore[assignment]
        host, port, tls = split_host_port(ctx.base_url, 636)
        if not host or not tls or not await check_dns(report, host):
            if not report.steps:
                report.add(
                    TestStep(
                        "dns",
                        "Địa chỉ máy chủ",
                        "fail",
                        "Cần địa chỉ ldaps://…",
                        "Ví dụ: ldaps://dc.congty.local:636",
                    )
                )
            return report
        if config.ca_bundle_pem.strip():
            report.add(
                TestStep("tls", "Kết nối TLS", "skip", "Dùng CA nội bộ: chứng chỉ được kiểm tra khi bind.")
            )
        else:
            await check_tcp_tls(report, host, port, tls=True, verify=True, connect_timeout=ctx.timeout)
            if report.failed():
                return report
        try:
            async with AdClient(ctx) as client:
                report.add(
                    TestStep("auth", "Bind LDAP", "ok", "Đã xác thực qua LDAPS (chứng chỉ được kiểm tra).")
                )
                rows = await client._t(
                    client._search,
                    config.base_dn,
                    "(&(objectCategory=person)(objectClass=user))",
                    ["sAMAccountName"],
                    limit=1,
                )
                report.add(
                    TestStep("read", "Tìm kiếm trong base DN", "ok", f"Đọc được ({len(rows)} bản ghi mẫu).")
                )
                base = await client._t(
                    client._search,
                    config.base_dn,
                    "(objectClass=*)",
                    ["allowedChildClassesEffective"],
                    scope=BASE,
                    limit=1,
                )
        except ConnectorHttpError as exc:
            key, label = (
                ("auth", "Bind LDAP")
                if not report.steps or report.steps[-1].key != "auth"
                else ("read", "Tìm kiếm trong base DN")
            )
            report.add(TestStep(key, label, "fail", exc.message, exc.hint))
            return report
        allowed = (base[0].get("allowedChildClassesEffective") if base else None) or []
        if not allowed:
            report.permissions.append(
                PermissionCheck(
                    "Tạo người dùng (create user)", None, "Không đọc được allowedChildClassesEffective."
                )
            )
        else:
            names = {str(a).lower() for a in allowed}
            report.permissions.append(PermissionCheck("Tạo người dùng (create user)", "user" in names))
        return report
