"""Outcome tracking: what the employer did after we applied.

The funnel used to end at "sent". The outcome (pending/screen/interview/offer/
rejected/ghosted) now lives on each persona's own job row, set only once the row
is applied, and the response funnel at /api/jobs/meta/outcomes reads across every
persona table.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.db import session as sess
from app.db.enums import ApplicationOutcome, JobStatus
from app.db.job_tables import allocate_job_id, job_model
from app.db.models import utcnow
from app.db.session import get_session_factory, init_db


async def _fresh_app(tmp_path, monkeypatch):
    db_file = tmp_path / "outcomes.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()
    from app.main import create_app

    return create_app(), db_file


async def _applied_row(persona_id: str = "brian_gou", company: str = "Acme", seq: int = 1) -> int:
    """A job row already submitted for this persona."""
    factory = get_session_factory()
    async with factory() as db:
        model = job_model(persona_id)
        jid = await allocate_job_id(db, persona_id, company.lower())
        db.add(
            model(
                id=jid,
                seq_no=seq,
                company=company,
                normalized_company=company.lower(),
                title="Senior Data Engineer",
                url=f"https://boards.greenhouse.io/{company.lower()}/jobs/{jid}",
                description="x" * 200,
                status=JobStatus.APPLIED.value,
                applied_at=utcnow(),
                folder_path="/tmp/whatever",
            )
        )
        await db.commit()
        return jid


@pytest.mark.asyncio
async def test_record_and_read_back_an_outcome(tmp_path, monkeypatch):
    app, _ = await _fresh_app(tmp_path, monkeypatch)
    job_id = await _applied_row()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/api/jobs/{job_id}/outcome",
            json={"outcome": "interview", "persona_id": "brian_gou", "notes": "Recruiter call Tue"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outcome"] == "interview"
        assert body["outcome_notes"] == "Recruiter call Tue"
        assert body["outcome_at"] is not None

        stats = (await client.get("/api/jobs/meta/outcomes")).json()
        profile = stats["by_profile"]["brian_gou"]
        assert profile["applied"] == 1
        assert profile["responses"] == 1
        assert profile["response_rate"] == 1.0


@pytest.mark.asyncio
async def test_outcome_survives_a_reread(tmp_path, monkeypatch):
    app, _ = await _fresh_app(tmp_path, monkeypatch)
    job_id = await _applied_row()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.patch(
            f"/api/jobs/{job_id}/outcome",
            json={"outcome": "rejected", "persona_id": "brian_gou"},
        )
    factory = get_session_factory()
    async with factory() as db:
        row = (await db.execute(select(job_model("brian_gou")))).scalars().one()
        assert row.outcome == ApplicationOutcome.REJECTED.value


@pytest.mark.asyncio
async def test_invalid_outcome_is_rejected(tmp_path, monkeypatch):
    app, _ = await _fresh_app(tmp_path, monkeypatch)
    job_id = await _applied_row()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/api/jobs/{job_id}/outcome",
            json={"outcome": "hired_maybe", "persona_id": "brian_gou"},
        )
        assert resp.status_code == 400
        assert "invalid_outcome" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cannot_record_an_outcome_for_an_unsent_application(tmp_path, monkeypatch):
    app, _ = await _fresh_app(tmp_path, monkeypatch)
    factory = get_session_factory()
    async with factory() as db:
        model = job_model("brian_gou")
        jid = await allocate_job_id(db, "brian_gou", "beta")
        db.add(
            model(
                id=jid,
                seq_no=1,
                company="Beta",
                normalized_company="beta",
                title="Data Engineer",
                url="https://example.com/1",
                description="x" * 200,
                status=JobStatus.RESUME_READY.value,
            )
        )
        await db.commit()
        job_id = jid

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/api/jobs/{job_id}/outcome",
            json={"outcome": "interview", "persona_id": "brian_gou"},
        )
        assert resp.status_code == 400
        assert "not_applied" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_response_rate_excludes_pending(tmp_path, monkeypatch):
    """Applications still waiting have not had their chance and must not dilute."""
    app, _ = await _fresh_app(tmp_path, monkeypatch)
    ids = [await _applied_row(company=f"Co{i}", seq=i + 1) for i in range(4)]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.patch(
            f"/api/jobs/{ids[0]}/outcome",
            json={"outcome": "interview", "persona_id": "brian_gou"},
        )
        await client.patch(
            f"/api/jobs/{ids[1]}/outcome",
            json={"outcome": "rejected", "persona_id": "brian_gou"},
        )
        # ids[2] and ids[3] stay pending
        profile = (await client.get("/api/jobs/meta/outcomes")).json()["by_profile"]["brian_gou"]
        assert profile["applied"] == 4
        assert profile["resolved"] == 2
        assert profile["responses"] == 1
        assert profile["response_rate"] == 0.5
