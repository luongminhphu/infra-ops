"""Exchange (pypsrp) and Active Directory (ldap3) connectors, with fakes – no real systems."""

from __future__ import annotations

import ssl
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from app.core.connectors import active_directory as ad
from app.core.connectors import exchange as ex
from app.core.connectors.base import ConnectionContext, Purpose
from app.core.connectors.http import ConnectorHttpError
from app.core.connectors.registry import load_builtin_connectors

SECRET = "S3cr3t-Pa55!"
CA = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"


def make_ctx(config: Any, url: str) -> ConnectionContext:
    return ConnectionContext(
        base_url=url,
        config=config,
        purpose=Purpose.READ,
        auth_type="username_password",
        public={"username": "svc"},
        secrets={"password": SECRET},
    )


def test_registered() -> None:
    from app.core.connectors.registry import get_connector

    load_builtin_connectors()
    for key in ("exchange", "active_directory"):
        c = get_connector(key)
        assert c is not None and not c.personal_credentials


# ---------------- Exchange ----------------


class FakePool:
    opened = closed = False

    def __init__(self, wsman: Any, **kw: Any) -> None:
        self.kw = kw

    def open(self) -> None:
        FakePool.opened = True

    def close(self) -> None:
        FakePool.closed = True


class FakePS:
    calls: ClassVar[list[tuple[str, list[tuple[str, Any]]]]] = []
    fail: Exception | None = None

    def __init__(self, pool: Any) -> None:
        self.cmd = ""
        self.params: list[tuple[str, Any]] = []
        self.had_errors = False
        self.streams = type("S", (), {"error": []})()

    def add_cmdlet(self, name: str) -> FakePS:
        self.cmd = name
        return self

    def add_parameter(self, k: str, v: Any = None) -> FakePS:
        self.params.append((k, v))
        return self

    def invoke(self) -> list[Any]:
        FakePS.calls.append((self.cmd, self.params))
        if FakePS.fail:
            raise FakePS.fail
        return [type("O", (), {"adapted_properties": {"Name": "a"}, "extended_properties": {}})()]


@pytest.fixture
def fake_ps(monkeypatch: pytest.MonkeyPatch) -> type[FakePS]:
    import pypsrp.powershell
    import pypsrp.wsman

    FakePS.calls, FakePS.fail = [], None
    monkeypatch.setattr(pypsrp.powershell, "RunspacePool", FakePool)
    monkeypatch.setattr(pypsrp.powershell, "PowerShell", FakePS)
    monkeypatch.setattr(pypsrp.wsman, "WSMan", lambda *a, **kw: object())
    return FakePS


EX_URL = "https://exch.local/PowerShell"


def test_basic_over_http_rejected() -> None:
    with pytest.raises(ex.ExchangeError):
        ex.ExchangeClient(make_ctx(ex.ExchangeConfig(auth="basic"), "http://exch.local/PowerShell"))
    ex.ExchangeClient(make_ctx(ex.ExchangeConfig(auth="basic"), EX_URL))
    with pytest.raises(ValidationError):
        ex.ExchangeConfig(operation_timeout_seconds=601)


async def test_allow_list_and_params_as_data(fake_ps: type[FakePS]) -> None:
    async with ex.ExchangeClient(make_ctx(ex.ExchangeConfig(), EX_URL)) as c:
        with pytest.raises(ex.ExchangeError):
            await c.run("Remove-Mailbox", {"Identity": "x"})
        assert fake_ps.calls == []
        evil = "bob; Remove-Mailbox -Identity all"
        await c.enable_remote_mailbox(evil, "bob@x.mail.onmicrosoft.com")
        await c.run("Get-RemoteMailbox", {"ResultSize": 1, "Verbose": True, "Off": False})
    assert fake_ps.calls[0] == (
        "Enable-RemoteMailbox",
        [("Identity", evil), ("RemoteRoutingAddress", "bob@x.mail.onmicrosoft.com")],
    )
    assert fake_ps.calls[1][1] == [("ResultSize", 1), ("Verbose", None)]  # switch has no value
    assert FakePool.closed


async def test_error_hides_password(fake_ps: type[FakePS]) -> None:
    fake_ps.fail = RuntimeError(f"401 credentials {SECRET}")
    async with ex.ExchangeClient(make_ctx(ex.ExchangeConfig(), EX_URL)) as c:
        with pytest.raises(ConnectorHttpError) as ei:
            await c.run("Get-RemoteMailbox", {"Identity": "a"})
    assert SECRET not in ei.value.message + ei.value.hint
    assert "Xác thực" in ei.value.message


async def test_exchange_test_connection_read_only(
    fake_ps: type[FakePS], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def dns(report: Any, host: str) -> list[str]:
        return ["1.1.1.1"]

    monkeypatch.setattr(ex, "check_dns", dns)
    report = await ex.ExchangeConnector().test_connection(
        make_ctx(ex.ExchangeConfig(ca_bundle_pem=CA), EX_URL)
    )
    assert report.ok
    assert [c for c, _ in fake_ps.calls] == ["Get-RemoteMailbox"]
    assert report.permissions[0].granted is None
    assert SECRET not in str(report.to_dict())


# ---------------- Active Directory ----------------


class FakeConn:
    last: FakeConn
    bind_ok = True

    def __init__(self, server: Any, **kw: Any) -> None:
        FakeConn.last = self
        self.server, self.kw = server, kw
        self.ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.response: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {"result": 0, "description": "success"}
        self.uac: int | None = None

    def bind(self) -> bool:
        return FakeConn.bind_ok

    def unbind(self) -> None:
        pass

    def search(self, base: str, flt: str, **kw: Any) -> bool:
        self.ops.append(("search", (base, flt), kw))
        if kw["attributes"] == ["allowedChildClassesEffective"]:
            self.response = [
                {
                    "type": "searchResEntry",
                    "dn": base,
                    "attributes": {"allowedChildClassesEffective": ["user", "group"]},
                }
            ]
        elif self.uac is not None:
            self.response = [
                {
                    "type": "searchResEntry",
                    "dn": base,
                    "attributes": {"userAccountControl": self.uac, "memberOf": []},
                }
            ]
        else:
            self.response = [
                {"type": "searchResEntry", "dn": "CN=a,DC=x", "attributes": {"sAMAccountName": "a"}}
            ]
        return True

    def add(self, *a: Any, **kw: Any) -> bool:
        self.ops.append(("add", a, kw))
        return True

    def modify(self, *a: Any, **kw: Any) -> bool:
        self.ops.append(("modify", a, kw))
        return True

    def modify_dn(self, *a: Any, **kw: Any) -> bool:
        self.ops.append(("modify_dn", a, kw))
        return True


@pytest.fixture
def fake_ldap(monkeypatch: pytest.MonkeyPatch) -> type[FakeConn]:
    FakeConn.bind_ok = True
    monkeypatch.setattr(ad, "Connection", FakeConn)
    return FakeConn


AD_URL = "ldaps://dc.local:636"


def ad_ctx(**kw: Any) -> ConnectionContext:
    return make_ctx(ad.AdConfig(base_dn="DC=corp,DC=local", **kw), AD_URL)


def test_tls_requires_cert() -> None:
    assert ad.build_tls(CA).validate == ssl.CERT_REQUIRED
    assert ad.build_tls("").validate == ssl.CERT_REQUIRED


def test_inside() -> None:
    assert ad.inside("CN=a, OU=Ban_Users ,DC=x", "ou=ban_users,dc=x")
    assert not ad.inside("CN=a,OU=Other,DC=x", "OU=Ban_Users,DC=x")
    assert ad.inside("OU=Users,DC=corp,DC=local", "OU=Users,DC=corp,DC=local")
    assert not ad.inside("CN=a\\,OU=Users,DC=corp,DC=local", "OU=Users,DC=corp,DC=local")
    assert not ad.inside("garbage", "OU=Users,DC=corp,DC=local")


def test_random_password_strong() -> None:
    p = ad.random_password()
    assert len(p) >= 24 and p != ad.random_password()


async def test_filter_escaping_and_validation(fake_ldap: type[FakeConn]) -> None:
    async with ad.AdClient(ad_ctx()) as c:
        with pytest.raises(ValueError):
            await c.find_users("1)(uid=*")
        await c.find_users("003551")
        await c.exists_any("a*)(uid=*", None, "x@y.com")
    flts = [op[1][1] for op in fake_ldap.last.ops]
    assert "(employeeID=003551)" in flts[0]
    assert "a\\2a\\29\\28uid=\\2a" in flts[1] and "a*)(uid=*" not in flts[1]
    assert "(proxyAddresses=smtp:x@y.com)" in flts[1] and "(proxyAddresses=SMTP:x@y.com)" in flts[1]


async def test_add_user_escapes_dn(fake_ldap: type[FakeConn]) -> None:
    async with ad.AdClient(ad_ctx()) as c:
        dn = await c.add_user("OU=Users,DC=x", "Nguyễn, Văn", {"sAMAccountName": "nvan"})
    assert dn == "CN=Nguyễn\\, Văn,OU=Users,DC=x"
    assert fake_ldap.last.ops[0][0] == "add" and fake_ldap.last.ops[0][1][0] == dn


async def test_set_password_not_leaked(fake_ldap: type[FakeConn]) -> None:
    async with ad.AdClient(ad_ctx()) as c:
        assert await c.set_password_random("CN=a,DC=x") is None
    changes = fake_ldap.last.ops[0][1][1]["unicodePwd"][0][1][0]
    assert changes.startswith('"'.encode("utf-16-le")) and len(changes) >= 26 * 2


async def test_disable_enable_uac(fake_ldap: type[FakeConn]) -> None:
    async with ad.AdClient(ad_ctx()) as c:
        fake_ldap.last.uac = 512
        await c.disable("CN=a,DC=x")
        fake_ldap.last.uac = 514
        await c.disable("CN=a,DC=x")  # already disabled: no write
        await c.enable("CN=a,DC=x")
        fake_ldap.last.uac = 66050
        await c.enable("CN=a,DC=x")
        await c.move("CN=a\\, b,OU=Users,DC=x", "OU=Ban_Users,DC=x")
        await c.move("CN=a\\, b,OU=Users,DC=x", "OU=Ban_Users,DC=x", "CN=a\\, b (002100)")
    mods = [op[1][1]["userAccountControl"][0][1] for op in fake_ldap.last.ops if op[0] == "modify"]
    assert mods == [[514], [512], [66048]]
    moves = [op for op in fake_ldap.last.ops if op[0] == "modify_dn"]
    assert moves[0][1][1] == "CN=a\\, b" and moves[0][2] == {"new_superior": "OU=Ban_Users,DC=x"}
    assert moves[1][1][1] == "CN=a\\, b (002100)"  # renamed on the way (duplicate CN in the target)


async def test_bind_failure_hides_password(fake_ldap: type[FakeConn]) -> None:
    fake_ldap.bind_ok = False
    with pytest.raises(ConnectorHttpError) as ei:
        async with ad.AdClient(ad_ctx()):
            pass
    assert SECRET not in ei.value.message + ei.value.hint


async def test_ad_test_connection_read_only(
    fake_ldap: type[FakeConn], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def dns(report: Any, host: str) -> list[str]:
        return ["1.1.1.1"]

    monkeypatch.setattr(ad, "check_dns", dns)
    report = await ad.AdConnector().test_connection(ad_ctx(ca_bundle_pem=CA))
    assert report.ok and report.permissions[0].granted is True
    assert {op[0] for op in fake_ldap.last.ops} == {"search"}
    assert SECRET not in str(report.to_dict())


async def test_ad_rejects_domain_backslash_user(fake_ldap: type[FakeConn]) -> None:
    ctx = make_ctx(ad.AdConfig(base_dn="DC=corp,DC=local"), AD_URL)
    ctx.public["username"] = "CORP\\svc"
    with pytest.raises(ad.AdError) as ei:
        async with ad.AdClient(ctx):
            pass
    assert "UPN" in ei.value.message and SECRET not in ei.value.message


async def test_bind_failure_reports_code_and_closes(fake_ldap: type[FakeConn]) -> None:
    fake_ldap.bind_ok = False
    closed: list[int] = []
    orig_init = FakeConn.__init__

    def init(self: FakeConn, *a: Any, **kw: Any) -> None:
        orig_init(self, *a, **kw)
        self.result = {"result": 49, "description": "invalidCredentials", "message": "... data 52e, v4563"}
        self.unbind = lambda: closed.append(1)  # type: ignore[method-assign]

    FakeConn.__init__ = init  # type: ignore[method-assign]
    try:
        with pytest.raises(ad.AdError) as ei:
            async with ad.AdClient(ad_ctx()):
                pass
    finally:
        FakeConn.__init__ = orig_init  # type: ignore[method-assign]
    assert "49" in ei.value.message and "sai mật khẩu" in ei.value.message and SECRET not in ei.value.message
    assert closed == [1]


async def test_thread_errors_become_ad_error(fake_ldap: type[FakeConn]) -> None:
    async with ad.AdClient(ad_ctx()) as c:
        fake_ldap.last.search = lambda *a, **k: (_ for _ in ()).throw(KeyError(SECRET))  # type: ignore[method-assign]
        with pytest.raises(ad.AdError) as ei:
            await c.find_users("003551")
    assert "KeyError" in ei.value.message and SECRET not in ei.value.message + ei.value.hint


def test_flat_casts_ints_and_server_has_no_info() -> None:
    row = ad._flat(
        {"dn": "x", "attributes": {"userAccountControl": "514", "adminCount": ["1"], "memberOf": "a"}}
    )
    assert row["userAccountControl"] == 514 and row["adminCount"] == 1 and row["memberOf"] == ["a"]


async def test_modify_none_replaces_with_empty(fake_ldap: type[FakeConn]) -> None:
    async with ad.AdClient(ad_ctx()) as c:
        await c.modify("CN=a,DC=x", {"mail": None})
    assert fake_ldap.last.ops[0][1][1] == {"mail": [(ad.MODIFY_REPLACE, [])]}
    assert fake_ldap.last.server.get_info == ad.NONE  # no schema download


def test_basic_requires_verified_tls() -> None:
    with pytest.raises(ex.ExchangeError):
        ex.ExchangeClient(make_ctx(ex.ExchangeConfig(auth="basic", verify_tls=False), EX_URL))


async def test_not_found_detected_from_error_record(
    fake_ps: type[FakePS], monkeypatch: pytest.MonkeyPatch
) -> None:
    def ps_init(self: FakePS, pool: Any) -> None:
        self.cmd, self.params = "", []
        self.had_errors = True
        rec = type(
            "R", (), {"fq_error": "ManagementObjectNotFoundException,Get-RemoteMailbox", "category": 13}
        )()
        self.streams = type("S", (), {"error": [rec]})()

    monkeypatch.setattr(FakePS, "__init__", ps_init)
    async with ex.ExchangeClient(make_ctx(ex.ExchangeConfig(), EX_URL)) as c:
        assert await c.get_remote_mailbox("nobody") is None
        with pytest.raises(ex.ExchangeNotFound):
            await c.run("Get-RemoteMailbox", {"Identity": "nobody"})


async def test_other_exchange_errors_still_raise(
    fake_ps: type[FakePS], monkeypatch: pytest.MonkeyPatch
) -> None:
    def ps_init(self: FakePS, pool: Any) -> None:
        self.cmd, self.params = "", []
        self.had_errors = True
        rec = type("R", (), {"fq_error": "Other", "category": 1, "__str__": lambda s: "boom"})()
        self.streams = type("S", (), {"error": [rec]})()

    monkeypatch.setattr(FakePS, "__init__", ps_init)
    async with ex.ExchangeClient(make_ctx(ex.ExchangeConfig(), EX_URL)) as c:
        with pytest.raises(ex.ExchangeError):
            await c.get_remote_mailbox("x")


def test_real_wsman_accepts_client_kwargs() -> None:
    from pypsrp.wsman import WSMan

    # Same kwargs ExchangeClient._open passes; constructing WSMan opens no connection.
    WSMan(
        "exch.local", port=443, ssl=True, path="PowerShell", username="svc", password="x", auth="ntlm",
        cert_validation=True, connection_timeout=5, operation_timeout=120, read_timeout=130,
    )  # fmt: skip


async def test_exchange_failure_logged_without_message(fake_ps: type[FakePS]) -> None:
    from structlog.testing import capture_logs

    fake_ps.fail = RuntimeError(f"secret {SECRET}")
    async with ex.ExchangeClient(make_ctx(ex.ExchangeConfig(), EX_URL)) as c:
        with capture_logs() as logs, pytest.raises(ex.ExchangeError):
            await c.run("Get-RemoteMailbox", {"Identity": "a"})
    assert (
        logs and "RuntimeError" in str(logs) and "Get-RemoteMailbox" in str(logs) and SECRET not in str(logs)
    )


async def test_group_members_add_remove_idempotent(fake_ldap: type[FakeConn]) -> None:
    async with ad.AdClient(ad_ctx()) as c:
        conn = fake_ldap.last
        await c.add_member("CN=g,DC=x", "CN=u,DC=x")
        await c.remove_member("CN=g,DC=x", "CN=u,DC=x")
        for code in (20, 68):
            conn.result = {"result": code, "description": "exists"}
            await c.add_member("CN=g,DC=x", "CN=u,DC=x")
        for code in (16, 53):
            conn.result = {"result": code, "description": "missing"}
            await c.remove_member("CN=g,DC=x", "CN=u,DC=x")
        conn.result = {"result": 50, "description": "insufficientAccessRights"}
        with pytest.raises(ad.AdError):
            await c.add_member("CN=g,DC=x", "CN=u,DC=x")
        with pytest.raises(ad.AdError):
            await c.remove_member("CN=g,DC=x", "CN=u,DC=x")
    mods = [op for op in conn.ops if op[0] == "modify"]
    assert mods[0][1] == ("CN=g,DC=x", {"member": [("MODIFY_ADD", ["CN=u,DC=x"])]})
    assert mods[1][1] == ("CN=g,DC=x", {"member": [("MODIFY_DELETE", ["CN=u,DC=x"])]})
    assert all(m[1][1]["member"][0][0] != "MODIFY_REPLACE" for m in mods)


async def test_get_group_and_user_attrs(fake_ldap: type[FakeConn]) -> None:
    assert {"title", "department", "manager"} <= set(ad.USER_ATTRS)
    async with ad.AdClient(ad_ctx()) as c:
        fake_ldap.last.response = []
        fake_ldap.last.search = lambda base, flt, **kw: (  # type: ignore[method-assign]
            setattr(
                fake_ldap.last,
                "response",
                [
                    {
                        "type": "searchResEntry",
                        "dn": base,
                        "attributes": {"objectClass": ["top", "group"], "adminCount": "1", "cn": "g"},
                    }
                ],
            )
            or True
        )
        g = await c.get_group("CN=g,DC=x")
    assert g == {"dn": "CN=g,DC=x", "objectClass": ["top", "group"], "adminCount": 1, "cn": "g"}
