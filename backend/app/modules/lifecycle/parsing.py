"""Parse RTR request descriptions ("label: value" lines, delivered as HTML by SDP)."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

from app.modules.lifecycle.settings import FIELD_TITLES, REQUIRED_FIELDS, EventKind, LifecycleSettings

_BLOCK_TAGS = re.compile(r"<\s*(br|/p|/div|/li|/tr|/h\d)\b[^>]*>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_NULLS = {"", "null", "none", "n/a", "-"}


def fold(text: str) -> str:
    """Lower-case and strip Vietnamese diacritics (đ → d) for tolerant comparisons."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return re.sub(r"\s+", " ", "".join(c for c in decomposed if not unicodedata.combining(c))).strip().lower()


def name_key(name: str) -> tuple[str, ...]:
    """Name compared without accents, punctuation or word order: "Thinh. Nguyen Canh" == "NGUYEN CANH THINH"."""
    return tuple(sorted(re.findall(r"\w+", fold(name))))


def html_to_text(value: str) -> str:
    text = _BLOCK_TAGS.sub("\n", value or "")
    text = _TAGS.sub("", text)
    text = html.unescape(text).replace("\xa0", " ").replace("\r", "")
    return "\n".join(line.strip() for line in text.split("\n"))


def _find(text: str, label: str) -> str | None:
    # Optional numbering ("2." / "2.1."), label, colon, value until end of line.
    pattern = re.compile(
        rf"^\s*(?:\d+(?:\.\d+)*\.?\s*)?{re.escape(label)}\s*\??\s*:\s*(.*)$", re.IGNORECASE | re.MULTILINE
    )
    m = pattern.search(text)
    if not m:
        return None
    value = m.group(1).strip()
    return None if value.lower() in _NULLS else value


_EMP_IN_PARENS = re.compile(r"\((\d{3,12})\)\s*$")


def split_person(value: str | None) -> tuple[str | None, str | None]:
    """'Tôn Thất Phi Dũng (003501)' → ('Tôn Thất Phi Dũng', '003501')."""
    if not value:
        return None, None
    m = _EMP_IN_PARENS.search(value)
    if m:
        return value[: m.start()].strip() or None, m.group(1)
    return value.strip(), None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def classify_change_type(raw: str | None, settings: LifecycleSettings) -> EventKind | None:
    """Explicit mapping first, then keyword detection; None when unknown."""
    if not raw:
        return None
    folded = fold(raw)
    for key, kind in settings.change_type_map.items():
        if fold(key) == folded:
            return kind
    if "thu viec" in folded and "chinh thuc" in folded:
        return "probation_to_official"
    if any(k in folded for k in ("nghi viec", "thoi viec", "resign", "cham dut")):
        return "offboard"
    if any(k in folded for k in ("thuyen chuyen", "dieu chuyen", "luan chuyen", "transfer")):
        return "transfer"
    # RTR types: "Tân tuyển - TV" (probation hire), "Thực tập sinh/CTV" (intern / contractor) start work.
    onboard = (
        "nhan vien moi",
        "tuyen moi",
        "tan tuyen",
        "thuc tap",
        "ctv",
        "tiep nhan",
        "recruit",
        "onboard",
    )
    if any(k in folded for k in onboard):
        return "onboard"
    if "chinh thuc" in folded:  # RTR "Chính thức": probation ends
        return "probation_to_official"
    if "cap tren" in folded:  # RTR "Thay đổi cấp trên trực tiếp": no device or access change for IT
        return "ignore"
    return None


@dataclass
class ParsedRequest:
    fields: dict[str, str | None] = field(default_factory=dict)
    employee_id: str | None = None
    full_name: str | None = None
    change_type_raw: str | None = None
    kind: EventKind | None = None
    effective_date: date | None = None
    manager_name: str | None = None
    manager_employee_id: str | None = None
    errors: list[str] = field(default_factory=list)


def parse_rtr_description(description_html: str, settings: LifecycleSettings) -> ParsedRequest:
    text = html_to_text(description_html)
    out = ParsedRequest()
    for key in FIELD_TITLES:
        out.fields[key] = _find(text, settings.label(key))
    emp = (out.fields.get("employee_id") or "").strip()
    if emp and not re.fullmatch(r"[0-9A-Za-z]{2,20}", emp):
        out.errors.append(f"Mã nhân viên không hợp lệ: “{emp}”.")
        emp = ""
    out.employee_id = emp or None
    out.full_name = out.fields.get("full_name")
    out.change_type_raw = out.fields.get("change_type")
    out.kind = classify_change_type(out.change_type_raw, settings)
    raw_date = out.fields.get("effective_date")
    out.effective_date = parse_date(raw_date)
    if raw_date and out.effective_date is None:
        out.errors.append(f"Ngày hiệu lực không đúng định dạng dd/MM/yyyy: “{raw_date}”.")
    out.manager_name, out.manager_employee_id = split_person(out.fields.get("manager"))
    for key in REQUIRED_FIELDS:
        if not out.fields.get(key):
            out.errors.append(f"Thiếu “{settings.label(key)}” trong nội dung request.")
    if out.change_type_raw and out.kind is None:
        out.errors.append(
            f"Chưa phân loại được loại biến động “{out.change_type_raw}”. Gán ở Cấu hình module → Loại biến động."
        )
    return out
