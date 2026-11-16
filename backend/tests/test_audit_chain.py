from datetime import UTC, datetime

from app.core.audit.models import AuditLog
from app.core.audit.service import GENESIS_HASH, _hash_fields, compute_hash, diff


def _row(**kw):  # type: ignore[no-untyped-def]
    base = dict(
        at=datetime(2026, 11, 16, tzinfo=UTC),
        actor_id=None,
        actor_upn="a",
        actor_ip=None,
        module_id="core",
        system_type=None,
        action="x",
        target_type=None,
        target_id=None,
        target_label=None,
        before=None,
        after={"a": 1},
        result="success",
        message=None,
        correlation_id=None,
        prev_hash=GENESIS_HASH,
    )
    base.update(kw)
    return AuditLog(**base)


def test_hash_changes_when_content_changes() -> None:
    a = _row()
    b = _row(after={"a": 2})
    assert compute_hash(GENESIS_HASH, _hash_fields(a)) != compute_hash(GENESIS_HASH, _hash_fields(b))


def test_hash_depends_on_previous() -> None:
    a = _row()
    assert compute_hash(GENESIS_HASH, _hash_fields(a)) != compute_hash("1" * 64, _hash_fields(a))


def test_diff_keeps_only_changes() -> None:
    before, after = diff({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4})
    assert before == {"b": 2, "c": None}
    assert after == {"b": 3, "c": 4}
