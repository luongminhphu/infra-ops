import pytest
from fastapi import APIRouter

from app.core.auth.passwords import hash_password, password_problems, verify_password
from app.core.logging import REDACTED, redact
from app.core.modules.manifest import ModuleManifest, NavItem, Permission
from app.core.modules.registry import ModuleRegistry, load_manifest


def test_redact_nested_secret_keys() -> None:
    data = {
        "name": "x",
        "config": {"password": "p", "items": [{"api_key": "k", "ok": 1}]},
        "Authorization": "b",
    }
    out = redact(data)
    assert out["name"] == "x"
    assert out["config"]["password"] == REDACTED
    assert out["config"]["items"][0] == {"api_key": REDACTED, "ok": 1}
    assert out["Authorization"] == REDACTED


def test_password_hash_and_verify() -> None:
    h = hash_password("Correct-Horse-9")
    assert verify_password("Correct-Horse-9", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


def test_password_policy() -> None:
    assert password_problems("Abcdefghij1!", 12) == []
    assert len(password_problems("short", 12)) == 2


def test_permission_must_be_prefixed() -> None:
    with pytest.raises(ValueError):
        ModuleManifest(id="assets", name="A", permissions=[Permission("lifecycle.x.y", "bad")])


def test_registry_rejects_duplicate_permission() -> None:
    a = ModuleManifest(id="a", name="A", permissions=[Permission("a.x.view", "x")])
    with pytest.raises(ValueError):
        ModuleRegistry([a, a])


def test_nav_filtered_by_permission() -> None:
    m = ModuleManifest(
        id="a",
        name="A",
        routers=[APIRouter()],
        permissions=[Permission("a.x.view", "x"), Permission("a.y.view", "y")],
        nav=[NavItem("X", "/x", "box", "a.x.view"), NavItem("Y", "/y", "box", "a.y.view")],
    )
    reg = ModuleRegistry([m])
    nav = reg.nav_for({"a.x.view"})
    assert [i["path"] for i in nav[0]["items"]] == ["/x"]  # type: ignore[index]
    assert reg.nav_for(set()) == []


def test_load_lifecycle_manifest() -> None:
    m = load_manifest("lifecycle")
    assert m.id == "lifecycle"
    assert "sdp" in m.connectors_used


@pytest.mark.parametrize("bad", ["../etc", "not-a-module"])
def test_load_manifest_rejects_bad_ids(bad: str) -> None:
    with pytest.raises((ValueError, ModuleNotFoundError)):
        load_manifest(bad)
