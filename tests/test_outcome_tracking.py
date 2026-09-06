"""Outcome tracking: what the employer did after we applied.

Before this existed the funnel ended at "sent" — every one of 1,000+ rows in
applications read status='applied' and nothing else, so there was no signal
telling a resume that works from one that does not.
"""
from __future__ import annotations

import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.db import session as sess
from app.db.enums import ApplicationOutcome, PackageStatus
from app.db.models import Job, JobPackage
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


async def _applied_package(persona_id: str = "brian_gou", company: str = "Acme") -> int:
    """A job with one package already submitted."""
    factory = get_session_factory()
    async with factory() as db:
        job = Job(
            company=company,
            normalized_company=company.lower(),
            title="Senior Data Engineer",
            url=f"https://boards.greenhouse.io/{company.lower()}/jobs/1",
            description="x" * 200,
            persona_id=persona_id,
        )
        db.add(job)
        await db.flush()
        db.add(
            JobPackage(
                job_id=job.id,
                persona_id=persona_id,
                status=PackageStatus.APPLIED.value,
                folder_path="/tmp/whatever",
            )
        )
        await db.commit()
        return job.id


@pytest.mark.asyncio
async def test_outcome_columns_are_added_to_an_existing_db(tmp_path, monkeypatch):
    """The migration must run against a real DB that predates these columns."""
    # Build the real schema, then strip the new columns back off so this is a
    # faithful pre-migration database rather than a hand-rolled approximation.
    _, db_file = await _fresh_app(tmp_path, monkeypatch)
    await sess.get_engine().dispose()

    con = sqlite3.connect(db_file)
    con.execute("DROP INDEX IF EXISTS ix_job_packages_outcome")
    for column in ("applied_at", "outcome", "outcome_at", "outcome_notes"):
        con.execute(f"ALTER TABLE job_packages DROP COLUMN {column}")
    con.executescript(
        """
        INSERT INTO job_packages (job_id, persona_id, status, created_at, updated_at)
            VALUES (1, 'brian_gou', 'applied', '2026-09-01 09:00:00', '2026-09-01 10:00:00');
        INSERT INTO job_packages (job_id, persona_id, status, created_at, updated_at)
            VALUES (2, 'brian_gou', 'resume_ready', '2026-09-02 09:00:00', '2026-09-02 10:00:00');
        """
    )
    con.commit()
    cols_before = {r[1] for r in con.execute("PRAGMA table_info(job_packages)")}
    con.close()
    assert "outcome" not in cols_before, "fixture failed to simulate a legacy DB"

    sess._engine = None
    sess._session_factory = None
    await init_db()

    con = sqlite3.connect(db_file)
    cols = {r[1] for r in con.execute("PRAGMA table_info(job_packages)")}
    assert {"applied_at", "outcome", "outcome_at", "outcome_notes"} <= cols

    # Existing rows default to pending, and applied ones get a backfilled date.
    rows = dict(con.execute("SELECT status, outcome FROM job_packages").fetchall())
    assert rows["applied"] == "pending"
    applied_at = con.execute(
        "SELECT applied_at FROM job_packages WHERE status='applied'"
    ).fetchone()[0]
    assert applied_at is not None, "submitted packages must get a backfilled applied_at"
    not_applied = con.execute(
        "SELECT applied_at FROM job_packages WHERE status='resume_ready'"
    ).fetchone()[0]
    assert not_applied is None, "unsent packages must not claim a submission date"
    con.close()


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_path, monkeypatch):
    _, db_file = await _fresh_app(tmp_path, monkeypatch)
    await init_db()  # second run must not raise on existing columns
    con = sqlite3.connect(db_file)
    cols = {r[1] for r in con.execute("PRAGMA table_info(job_packages)")}
    assert "outcome" in cols
    con.close()


@pytest.mark.asyncio
async def test_record_and_read_back_an_outcome(tmp_path, monkeypatch):
    app, _ = await _fresh_app(tmp_path, monkeypatch)
    job_id = await _applied_package()

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
    job_id = await _applied_package()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.patch(
            f"/api/jobs/{job_id}/outcome",
            json={"outcome": "rejected", "persona_id": "brian_gou"},
        )
    factory = get_session_factory()
    async with factory() as db:
        pkg = (await db.execute(select(JobPackage))).scalars().one()
        assert pkg.outcome == ApplicationOutcome.REJECTED.value


@pytest.mark.asyncio
async def test_invalid_outcome_is_rejected(tmp_path, monkeypatch):
    app, _ = await _fresh_app(tmp_path, monkeypatch)
    job_id = await _applied_package()
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
        job = Job(
            company="Beta",
            normalized_company="beta",
            title="Data Engineer",
            url="https://example.com/1",
            description="x" * 200,
            persona_id="brian_gou",
        )
        db.add(job)
        await db.flush()
        db.add(
            JobPackage(
                job_id=job.id,
                persona_id="brian_gou",
                status=PackageStatus.RESUME_READY.value,
            )
        )
        await db.commit()
        job_id = job.id

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
    ids = [await _applied_package(company=f"Co{i}") for i in range(4)]
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
