"""Row scope of printable records: by creator / Job (U1, C-01, C-02)."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import select, text

from app.core.audit.service import Actor
from app.core.auth.models import User
from app.core.db import get_sessionmaker
from app.core.documents import service
from app.core.documents.models import Document
from app.core.workflow import engine
from app.core.workflow.models import Job, JobStep
from app.modules.lifecycle.documents import HANDOVER
from app.modules.lifecycle.stocktake_record import STOCKTAKE_RECORD
from tests.test_documents import handover_payload
from tests.test_workload import _user

SYSTEM = Actor(user_id=None, upn="system", ip=None)


async def _job_with_draft(assignee: User | None) -> tuple[str, str]:
    async with get_sessionmaker()() as s:
        job, _ = await engine.create_job(
            s, SYSTEM, "lifecycle.onboard", params={"employee_id": "009002"},
            subject_key="009002", subject_label="scope", external_ref=None,
        )  # fmt: skip
        if assignee:
            await engine.set_assignee(
                s, job, user_id=assignee.id, name=assignee.display_name, actor="test", source="manual"
            )
        doc = await service.create_draft(s, SYSTEM, HANDOVER, "lifecycle", handover_payload(), job_id=job.id)
        await s.commit()
        return str(job.id), str(doc.id)


async def test_documents_scoped_by_job_and_creator(admin, client) -> None:  # type: ignore[no-untyped-def]
    for role, upn in [("operator", "ds-a@test.local"), ("operator", "ds-b@test.local")]:
        await _user(admin, role, upn)
    await _user(admin, "supervisor", "ds-sup@test.local")
    async with get_sessionmaker()() as s:
        a = await s.scalar(select(User).where(User.upn == "ds-a@test.local"))
    job_id, doc_id = await _job_with_draft(a)

    await client.login("ds-b@test.local")
    seen = {d["id"] for d in (await client.get("/api/core/documents")).json()["items"]}
    assert doc_id not in seen
    base = f"/api/core/documents/{doc_id}"
    assert (await client.get(base)).status_code == 404
    assert (await client.get(f"{base}/preview.pdf")).status_code == 404
    assert (await client.get(f"{base}/file/pdf")).status_code == 404
    assert (await client.post(f"{base}/finalize")).status_code == 404
    assert (await client.post(f"/api/lifecycle/documents/{doc_id}/attach-sdp", json={})).status_code == 404
    # Own job-less record: visible to the creator only.
    mine = (
        await client.post(
            "/api/core/documents", json={"kind": "lifecycle.handover", "data": handover_payload()}
        )
    ).json()
    assert mine["job_id"] is None
    assert mine["id"] in {d["id"] for d in (await client.get("/api/core/documents")).json()["items"]}

    await client.login("ds-a@test.local")
    items = (await client.get("/api/core/documents")).json()
    assert doc_id in {d["id"] for d in items["items"]} and mine["id"] not in {d["id"] for d in items["items"]}
    assert (await client.get(base)).json()["job_id"] == job_id
    assert (await client.get(f"/api/core/documents/{mine['id']}")).status_code == 404
    fin = await client.post(f"{base}/finalize")  # C-01: the assignee finalizes the worker's draft
    assert fin.status_code == 200, fin.text
    assert fin.json()["status"] == "finalized"

    await client.login("ds-sup@test.local")
    seen = {d["id"] for d in (await client.get("/api/core/documents")).json()["items"]}
    assert {doc_id, mine["id"]} <= seen
    # view_all alone does not grant edits on someone else's job-less record.
    assert (await client.post(f"/api/core/documents/{mine['id']}/finalize")).status_code == 403


async def test_unassigned_job_draft_visible_and_stocktake_unscoped(admin, client) -> None:  # type: ignore[no-untyped-def]
    await _user(admin, "operator", "ds-c@test.local")
    _, doc_id = await _job_with_draft(None)
    await client.login("ds-c@test.local")
    assert (await client.get(f"/api/core/documents/{doc_id}")).status_code == 200

    class U:  # minimal CurrentUser stand-in
        upn = "x"
        id = uuid.uuid4()

        def has(self, code: str) -> bool:
            return False

    assert service.scope_clause(U(), STOCKTAKE_RECORD) is None  # type: ignore[arg-type]
    assert service.scope_clause(U(), HANDOVER) is not None  # type: ignore[arg-type]


async def test_migration_backfill_links_documents_to_jobs() -> None:
    path = Path(__file__).parents[1] / "migrations/core/versions/0011_core_document_job.py"
    spec = importlib.util.spec_from_file_location("mig0011", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    job_id, doc_id = await _job_with_draft(None)
    async with get_sessionmaker()() as s:
        doc = await s.get(Document, uuid.UUID(doc_id))
        assert doc is not None
        doc.job_id = None
        for seq, out in ((99, {"document_id": doc_id}), (98, {"document_id": "not-a-uuid"})):
            s.add(JobStep(job_id=uuid.UUID(job_id), seq=seq, key=f"k{seq}", title="t", kind="manual",
                          status="waiting", output=out))  # fmt: skip
        await s.flush()
        await s.execute(text(mod.BACKFILL_SQL))
        await s.refresh(doc)
        assert str(doc.job_id) == job_id
        await s.rollback()


async def _uid(upn: str) -> User:
    async with get_sessionmaker()() as s:
        return await s.scalar(select(User).where(User.upn == upn))  # type: ignore[return-value]


async def test_finalized_scope_mutations_flags_and_reassign(admin, client) -> None:  # type: ignore[no-untyped-def]
    for role, upn in [
        ("operator", "dq-a@test.local"),
        ("operator", "dq-b@test.local"),
        ("supervisor", "dq-sup@test.local"),
    ]:
        await _user(admin, role, upn)
    a, b = await _uid("dq-a@test.local"), await _uid("dq-b@test.local")
    job_id, doc_id = await _job_with_draft(a)
    base = f"/api/core/documents/{doc_id}"

    await client.login("dq-a@test.local")
    got = (await client.get(base)).json()
    assert got["can_edit"] is True and got["can_upload_signed"] is False  # draft: nothing to sign yet
    listed = next(d for d in (await client.get("/api/core/documents")).json()["items"] if d["id"] == doc_id)
    assert listed["can_edit"] is True
    fin = await client.post(f"{base}/finalize")
    assert fin.status_code == 200, fin.text
    assert fin.json()["can_upload_signed"] is True

    await client.login("dq-b@test.local")  # C-12: scope must hold after finalize, for files too
    for url in (f"{base}/file/pdf", f"{base}/file/docx"):
        assert (await client.get(url)).status_code == 404
    assert (await client.put(base, json={"data": handover_payload(), "version": 1})).status_code == 404
    assert (
        await client.post(f"{base}/signed", files={"file": ("s.pdf", b"%PDF-1.4", "application/pdf")})
    ).status_code == 404
    assert (await client.post(f"{base}/void", json={"reason": "không cần"})).status_code == 404

    # Reassign A -> B: B gains access, A loses it.
    async with get_sessionmaker()() as s:
        job = await s.get(Job, uuid.UUID(job_id))
        await engine.set_assignee(s, job, user_id=b.id, name=b.display_name, actor="test", source="manual")
        await s.commit()
    got = await client.get(base)
    assert got.status_code == 200 and got.json()["can_edit"] is True
    await client.login("dq-a@test.local")
    assert (await client.get(base)).status_code == 404


async def test_supervisor_flags_and_signed_upload(admin, client) -> None:  # type: ignore[no-untyped-def]
    await _user(admin, "operator", "dq-c@test.local")
    await _user(admin, "supervisor", "dq-sup@test.local")
    c = await _uid("dq-c@test.local")
    _, doc_id = await _job_with_draft(c)
    base = f"/api/core/documents/{doc_id}"
    await client.login("dq-sup@test.local")
    assert (await client.get(base)).json()["can_edit"] is True  # job-linked + create permission
    fin = await client.post(f"{base}/finalize")
    assert fin.status_code == 200, fin.text

    # Job-less finalized record of another creator: supervisor sees it and may upload the signed copy, not edit.
    await client.login("dq-c@test.local")
    mine = (
        await client.post(
            "/api/core/documents", json={"kind": "lifecycle.handover", "data": handover_payload()}
        )
    ).json()
    assert (await client.post(f"/api/core/documents/{mine['id']}/finalize")).status_code == 200
    await client.login("dq-sup@test.local")
    got = (await client.get(f"/api/core/documents/{mine['id']}")).json()
    assert got["can_edit"] is False and got["can_upload_signed"] is True
    up = await client.post(
        f"/api/core/documents/{mine['id']}/signed",
        files={"file": ("s.pdf", b"%PDF-1.4 x", "application/pdf")},
    )
    assert up.status_code == 200, up.text
    assert up.json()["status"] == "signed"
