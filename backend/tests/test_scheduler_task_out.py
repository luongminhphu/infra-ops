from __future__ import annotations

from app.core.scheduler.service import ScheduledTaskRow, task_out


def test_task_out_hides_raw_error_unless_privileged() -> None:
    row = ScheduledTaskRow(
        key="x.sync", module_id="x", interval_seconds=60, last_error="ldap://dc01.corp.local failed"
    )
    assert "dc01" not in str(task_out(row)["last_error"])
    assert task_out(row)["last_error"]
    assert task_out(row, show_error=True)["last_error"] == "ldap://dc01.corp.local failed"
    row.last_error = None
    assert task_out(row)["last_error"] is None
