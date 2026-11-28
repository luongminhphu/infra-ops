"""Spec of the accent-free fold (lifecycle, workload) and its SQL twin."""

from __future__ import annotations

import unicodedata

import pytest
from sqlalchemy import String, column, select, values

from app.core.db import get_sessionmaker
from app.core.text import fold as core_fold
from app.core.text import fold_sql
from app.modules.lifecycle.parsing import fold as lifecycle_fold
from app.modules.lifecycle.settings import fold as settings_fold
from app.modules.workload.quotes import fold as workload_fold

CASES = [
    ("Đặng Thị Ánh", "dang thi anh"),
    ("  NGUYỄN   Văn  A ", "nguyen van a"),
    ("Phòng IT\tTầng 5", "phong it tang 5"),
    (unicodedata.normalize("NFD", "Nguyễn Ơ"), "nguyen o"),
    ("ĐẠI", "dai"),
    ("", ""),
]


@pytest.mark.parametrize(("raw", "want"), CASES)
def test_lifecycle_fold_spec(raw: str, want: str) -> None:
    assert lifecycle_fold(raw) == settings_fold(raw) == core_fold(raw) == want


@pytest.mark.parametrize(("raw", "want"), CASES)
def test_workload_fold_agrees_up_to_whitespace(raw: str, want: str) -> None:
    # Callers of the workload fold strip/split on whitespace themselves.
    assert " ".join(workload_fold(raw).split()) == want


async def test_fold_sql_matches_python() -> None:
    names = ["Đặng Thị Ánh", "NGUYỄN Văn A", "Phòng IT Tầng 5", "ĐẠI", "Ưu Tiên Ở Ỹ", ""]
    v = values(column("n", String), name="t").data([(n,) for n in names])
    async with get_sessionmaker()() as s:
        rows = (await s.execute(select(v.c.n, fold_sql(v.c.n)))).all()
    assert len(rows) == len(names)
    for raw, folded in rows:
        assert folded == core_fold(raw)
