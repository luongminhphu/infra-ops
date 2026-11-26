"""Phase 3: document engine, numbering, handover/return lifecycle, templates, SDP attach."""

from __future__ import annotations

import io
import shutil
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from docx import Document as DocxDocument
from sqlalchemy import select

from app.core.audit.models import AuditLog
from app.core.db import get_sessionmaker
from app.core.documents.engine import render_docx, template_variables
from app.core.documents.service import next_number
from app.modules.lifecycle.documents import (
    HANDOVER,
    RETURN,
    HandoverData,
    ReturnData,
    handover_context,
    return_context,
)

needs_soffice = pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
PERIOD = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y%m")


def handover_payload(emp: str = "003551", name: str = "Nguyễn Văn An & Co <test>") -> dict:  # type: ignore[type-arg]
    return {
        "employee": {
            "id": emp,
            "full_name": name,
            "department": "Kinh Doanh",
            "job_title": "CV",
            "work_location": "CN1",
        },
        "date": "2026-11-16",
        "it_rep": "Lê Minh Tuấn",
        "reviewer": "Trần Văn Quản",
        "request_id": "1001",
        "assets": [{"name": "HSC-LT-0001", "tag": "LT0001", "date": "2026-11-16", "note": "Kèm sạc"}],
        "checklist": ["ok", "ok", "na"],
        "software": [True, True],
    }


def docx_text(data: bytes) -> str:
    doc = DocxDocument(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for cell in (c for row in t.rows for c in row.cells):
            parts.append(cell.text)
            for inner in cell.tables:
                parts.extend(c.text for r in inner.rows for c in r.cells)
    for s in doc.sections:
        parts.extend(p.text for p in s.header.paragraphs)
        for t in s.header.tables:
            parts.extend(c.text for r in t.rows for c in r.cells)
    return "\n".join(parts)


def test_bundled_templates_use_documented_placeholders() -> None:
    h = template_variables(HANDOVER.default_template.read_bytes())
    r = template_variables(RETURN.default_template.read_bytes())
    assert h == {
        "assets",
        "checklist",
        "date",
        "employee",
        "it_rep",
        "number",
        "reviewer",
        "software",
        "software_other",
    }
    assert r == {"assets", "checklist", "date", "employee", "it_rep", "number", "reviewer"}


def test_render_handover_fills_loop_checkboxes_and_escapes() -> None:
    data = HandoverData.model_validate(
        {
            **handover_payload(),
            "assets": [
                {"name": f"HSC-LT-{i:04d}", "tag": f"LT{i:04d}", "date": "2026-11-16"} for i in range(1, 8)
            ],
        }
    )
    out = render_docx(HANDOVER.default_template.read_bytes(), handover_context(data, "BG-202611-0042"))
    text = docx_text(out)
    assert "BG-202611-0042" in text
    assert "Nguyễn Văn An & Co <test>" in text  # escaped in XML, intact in text
    assert all(f"LT{i:04d}" in text for i in range(1, 8))  # 7 rows from a 1-row loop
    assert "{%" not in text and "{{" not in text
    assert "☑" in text
    zipfile.ZipFile(io.BytesIO(out)).testzip()


def test_return_context_conditions() -> None:
    data = ReturnData.model_validate(
        {
            **handover_payload(),
            "assets": [
                {"name": "A", "tag": "T1", "date": "2026-11-16", "condition": "damaged"},
                {"name": "B", "tag": "T2", "date": "2026-11-16", "condition": "not_returned"},
            ],
            "checklist": ["ok"],
        }
    )
    ctx = return_context(data, "TH-1")
    assert [a["condition"] for a in ctx["assets"]] == ["Hỏng", "Chưa thu hồi"]
    assert ctx["assets"][1]["date"] == ""  # not returned → no return date
    assert (
        len(ctx["checklist"]) == 3 and ctx["checklist"][0]["ok"] == "☑" and ctx["checklist"][2]["ok"] == "☐"
    )
    out = render_docx(RETURN.default_template.read_bytes(), ctx)
    assert "Chưa thu hồi" in docx_text(out)


async def test_numbering_is_sequential_per_prefix_and_month() -> None:
    async with get_sessionmaker()() as s:
        a = await next_number(s, "ZZ")
        b = await next_number(s, "ZZ")
        c = await next_number(s, "YY")
        await s.commit()
    assert a == f"ZZ-{PERIOD}-0001" and b == f"ZZ-{PERIOD}-0002" and c == f"YY-{PERIOD}-0001"


async def test_templates_seeded_and_upload_validation(admin) -> None:  # type: ignore[no-untyped-def]
    kinds = {k["key"]: k for k in (await admin.get("/api/core/document-kinds")).json()}
    assert (
        kinds["lifecycle.handover"]["active_version"] == 1 and kinds["lifecycle.handover"]["prefix"] == "BG"
    )
    versions = (await admin.get("/api/core/document-kinds/lifecycle.handover/templates")).json()
    assert (
        versions[0]["version"] == 1 and versions[0]["is_active"] and versions[0]["unknown_placeholders"] == []
    )

    bad = await admin.post(
        "/api/core/document-kinds/lifecycle.handover/templates",
        files={"file": ("x.docx", b"not a docx", "application/octet-stream")},
    )
    assert bad.json()["error"]["code"] == "unsupported_file"

    # A template with an unknown placeholder is accepted but flagged; syntax errors are rejected.
    tpl = DocxDocument()
    tpl.add_paragraph("{{ number }} {{ employee.full_name }} {{ bogus_field }}")
    buf = io.BytesIO()
    tpl.save(buf)
    up = await admin.post(
        "/api/core/document-kinds/lifecycle.handover/templates",
        files={
            "file": (
                "v2.docx",
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"note": "thử"},
    )
    assert up.status_code == 201, up.text
    assert (
        up.json()["version"] == 2
        and up.json()["unknown_placeholders"] == ["bogus_field"]
        and not up.json()["is_active"]
    )
    broken = DocxDocument()
    broken.add_paragraph("{{ number ")
    buf = io.BytesIO()
    broken.save(buf)
    err = await admin.post(
        "/api/core/document-kinds/lifecycle.handover/templates",
        files={"file": ("bad.docx", buf.getvalue(), "application/octet-stream")},
    )
    assert err.json()["error"]["code"] == "template_syntax"
    # activate v2 then back to v1
    assert (await admin.post("/api/core/document-kinds/lifecycle.handover/templates/2/activate")).json()[
        "is_active"
    ]
    assert (await admin.post("/api/core/document-kinds/lifecycle.handover/templates/1/activate")).json()[
        "is_active"
    ]
    dl = await admin.get("/api/core/document-kinds/lifecycle.handover/templates/1/file")
    assert dl.content.startswith(b"PK")


async def test_validation_and_permissions(admin, client) -> None:  # type: ignore[no-untyped-def]
    bad = await admin.post(
        "/api/core/documents",
        json={"kind": "lifecycle.handover", "data": {**handover_payload(), "assets": []}},
    )
    assert bad.json()["error"]["code"] == "invalid_document"

    doc = (
        await admin.post(
            "/api/core/documents", json={"kind": "lifecycle.handover", "data": handover_payload()}
        )
    ).json()
    upd = await admin.put(
        f"/api/core/documents/{doc['id']}",
        json={"data": {**handover_payload(), "it_rep": "Người Khác"}, "version": doc["version"]},
    )
    assert upd.status_code == 200, upd.text
    async with get_sessionmaker()() as s:
        entry = await s.scalar(
            select(AuditLog).where(AuditLog.action == "documents.updated", AuditLog.target_id == doc["id"])
        )
    assert entry is not None and entry.after == {"it_rep": "Người Khác"} and "it_rep" in entry.before

    await client.login("doc-viewer@test.local")
    assert (
        await client.post(
            "/api/core/documents", json={"kind": "lifecycle.handover", "data": handover_payload()}
        )
    ).status_code == 403


@needs_soffice
@respx.mock(assert_all_called=False)
async def test_handover_lifecycle_end_to_end(admin, respx_mock) -> None:  # type: ignore[no-untyped-def]
    created = await admin.post(
        "/api/core/documents", json={"kind": "lifecycle.handover", "data": handover_payload()}
    )
    assert created.status_code == 201, created.text
    doc = created.json()
    assert (
        doc["status"] == "draft"
        and doc["number"] is None
        and doc["subject_key"] == "003551"
        and doc["external_ref"] == "1001"
    )

    upd = await admin.put(
        f"/api/core/documents/{doc['id']}",
        json={"data": {**handover_payload(), "it_rep": "Người Khác"}, "version": doc["version"]},
    )
    assert upd.json()["data"]["it_rep"] == "Người Khác"
    stale = await admin.put(
        f"/api/core/documents/{doc['id']}", json={"data": handover_payload(), "version": doc["version"]}
    )
    assert stale.json()["error"]["code"] == "stale_version"

    preview = await admin.get(f"/api/core/documents/{doc['id']}/preview.pdf")
    assert preview.status_code == 200 and preview.content.startswith(b"%PDF")

    fin = (await admin.post(f"/api/core/documents/{doc['id']}/finalize")).json()
    assert fin["status"] == "finalized" and fin["number"].startswith(f"BG-{PERIOD}-") and fin["has_pdf"]
    locked = await admin.put(
        f"/api/core/documents/{doc['id']}", json={"data": handover_payload(), "version": fin["version"]}
    )
    assert locked.json()["error"]["code"] == "document_locked"
    docx = await admin.get(f"/api/core/documents/{doc['id']}/file/docx")
    assert fin["number"] in docx_text(docx.content)
    assert "attachment" in docx.headers["content-disposition"]

    # Signed scan: only PDF/PNG/JPEG by content, not by name
    fake = await admin.post(
        f"/api/core/documents/{doc['id']}/signed",
        files={"file": ("x.pdf", b"MZ\x90\x00evil", "application/pdf")},
    )
    assert fake.json()["error"]["code"] == "unsupported_file"
    signed = await admin.post(
        f"/api/core/documents/{doc['id']}/signed",
        files={"file": ("scan.pdf", b"%PDF-1.4\n%fake scan\n", "application/pdf")},
    )
    assert signed.json()["status"] == "signed"
    got = await admin.get(f"/api/core/documents/{doc['id']}/file/signed")
    assert got.content.startswith(b"%PDF") and got.headers["x-content-type-options"] == "nosniff"

    # Attach to SDP runs as the person: their own API key, never the shared Write credential
    no_write = await admin.post(f"/api/lifecycle/documents/{doc['id']}/attach-sdp", json={})
    assert no_write.json()["error"]["code"] in ("capability_unavailable", "personal_credential_missing")
    existing = {p["name"]: p for p in (await admin.get("/api/core/connections")).json()}
    sdp = (
        existing.get("SDP Docs")
        or (
            await admin.post(
                "/api/core/connections",
                json={
                    "system_type": "sdp",
                    "name": "SDP Docs",
                    "base_url": "https://sdp-docs.test.local",
                    "config": {},
                },
            )
        ).json()
    )
    await admin.put(
        f"/api/core/connections/{sdp['id']}/credentials/write",
        json={"auth_type": "api_key", "secrets": {"api_key": "w-0000000000001"}},
    )
    settings = (await admin.get("/api/lifecycle/settings")).json()["values"]
    await admin.put("/api/lifecycle/settings", json={**settings, "sdp_profile_id": sdp["id"]})
    upload = respx_mock.post("https://sdp-docs.test.local/api/v3/requests/1001/_uploads").mock(
        return_value=httpx.Response(200, json={"response_status": {"status_code": 2000, "status": "success"}})
    )
    shared_only = await admin.post(f"/api/lifecycle/documents/{doc['id']}/attach-sdp", json={})
    assert shared_only.json()["error"]["code"] == "personal_credential_missing"
    assert "Khoá API của tôi" in shared_only.json()["error"]["message"]
    mine = await admin.put(
        f"/api/core/me/credentials/{sdp['id']}",
        json={"auth_type": "api_key", "secrets": {"api_key": "me-00000000042"}},
    )
    assert mine.status_code == 200 and mine.json()["hint"] == "0042" and "me-00000000042" not in mine.text
    ok = await admin.post(f"/api/lifecycle/documents/{doc['id']}/attach-sdp", json={})
    assert ok.status_code == 200, ok.text
    assert upload.called and b"%fake scan" in upload.calls[0].request.content
    assert (
        upload.calls[0].request.headers["authtoken"] == "me-00000000042"
    )  # SDP sees the person, not a shared key
    assert ok.json()["events"][-1]["ok"] is True

    # Listing / search
    listed = (await admin.get("/api/core/documents", params={"q": fin["number"]})).json()
    assert listed["total"] == 1 and listed["items"][0]["status"] == "signed"

    # Void a numbered record requires a reason (admin has manage)
    voided = await admin.post(f"/api/core/documents/{doc['id']}/void", json={"reason": "Lập nhầm người nhận"})
    assert voided.json()["status"] == "void"


@needs_soffice
async def test_sample_pdf_and_return_finalize_numbering(admin) -> None:  # type: ignore[no-untyped-def]
    sample = await admin.get("/api/core/document-kinds/lifecycle.return/templates/1/sample.pdf")
    assert sample.status_code == 200 and sample.content.startswith(b"%PDF")
    payload = {
        **handover_payload(emp="002100", name="Phạm Thị Bình"),
        "assets": [
            {
                "name": "HSC-LT-0009",
                "tag": "LT0009",
                "date": "2026-11-16",
                "condition": "missing_accessory",
                "note": "Thiếu sạc",
            }
        ],
    }
    payload.pop("software")
    numbers = []
    for _ in range(2):
        d = (
            await admin.post("/api/core/documents", json={"kind": "lifecycle.return", "data": payload})
        ).json()
        numbers.append((await admin.post(f"/api/core/documents/{d['id']}/finalize")).json()["number"])
    a, b = (int(n.rsplit("-", 1)[1]) for n in numbers)
    assert numbers[0].startswith(f"TH-{PERIOD}-") and b == a + 1
