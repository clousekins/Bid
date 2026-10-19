"""Rejecting a listing by hand, and the per-person company gate.

A rejected job stays on the list and stays readable. What it stops doing is
taking part: no batch picks it up, and it no longer holds its company against the
next job that comes from there.

The company "already applied" gate is per person now: each persona owns its own
jobs table, so a company closes to ingest for the persona that applied there, and
reopens when that apply is rejected or ages past the 30-day window. Which personas
a listing is even offered to is decided by country (Brazil → Bruno, US → the rest)
in ``ingest_listing_for_personas``.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session as sess
from app.db.enums import JobStatus, can_transition
from app.db.job_tables import job_model
from app.db.models import Application, BackgroundJob, utcnow
from app.db.session import get_session_factory, init_db
from app.services.pipeline import (
    company_is_applied,
    ingest_listing_for_personas,
    process_listing,
)
from app.validators.dedupe import normalize_company

REJECTED = JobStatus.REJECTED.value
PROFILE = "alex_mak"
OTHER = "brian_gou"


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    db_file = tmp_path / "rejected.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()
    return get_session_factory()


async def _add(
    session,
    company: str,
    url: str,
    title: str = "Data Engineer",
    location: str = "Remote, US",
    pid: str = PROFILE,
):
    result = await process_listing(
        session,
        {"company": company, "title": title, "url": url, "location": location},
        persona_id=pid,
        skip_content_gates=True,
    )
    await session.commit()
    assert result.ok, result.reason
    return result.job


async def _age(session, job, days: int = 11) -> None:
    """Push a listing past the one-per-company window so a sibling can ingest."""
    job.created_at = utcnow() - timedelta(days=days)
    await session.commit()


def _apply_row(session, job, persona: str) -> None:
    """Record ``persona`` as applied at this job's company (company-level)."""
    session.add(
        Application(
            persona_id=persona,
            company=job.company,
            normalized_company=normalize_company(job.company),
            job_id=job.id,
            status="applied",
        )
    )


class TestReachingTheStatus:
    """Rejecting overrules the pipeline, so it is reachable from anywhere."""

    @pytest.mark.parametrize(
        "current",
        [s for s in JobStatus if s is not JobStatus.REJECTED],
    )
    def test_any_status_can_be_rejected(self, current):
        assert can_transition(current, JobStatus.REJECTED)

    def test_including_the_ones_that_are_otherwise_terminal(self):
        for terminal in (JobStatus.APPLIED, JobStatus.BLOCKED, JobStatus.REJECTED_ONSITE):
            assert not can_transition(terminal, JobStatus.JD_READY)
            assert can_transition(terminal, JobStatus.REJECTED)

    def test_it_can_be_undone(self):
        assert can_transition(JobStatus.REJECTED, JobStatus.JD_READY)
        assert can_transition(JobStatus.REJECTED, JobStatus.NEW)

    def test_but_not_undone_straight_into_applied(self):
        assert not can_transition(JobStatus.REJECTED, JobStatus.APPLIED)


class TestThePersonCompanyGateReopens:
    async def test_another_persona_applied_does_not_block(self, factory):
        """Brian applying at Acme does not close Acme for Alex — separate tables."""
        async with factory() as session:
            placeholder = await _add(session, "Acme", "https://example.com/seed", pid=OTHER)
            _apply_row(session, placeholder, OTHER)
            await session.commit()

            result = await process_listing(
                session,
                {
                    "company": "Acme",
                    "title": "Staff Engineer",
                    "url": "https://example.com/b",
                    "location": "Remote, US",
                },
                persona_id=PROFILE,
                skip_content_gates=True,
            )
            await session.commit()
            assert result.ok, result.reason

    async def test_the_persona_applied_blocks_a_second_job(self, factory):
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a")
            _apply_row(session, first, PROFILE)
            await session.commit()

            result = await process_listing(
                session,
                {
                    "company": "Acme LLC",
                    "title": "Staff Engineer",
                    "url": "https://example.com/b",
                    "location": "Remote, US",
                },
                persona_id=PROFILE,
                skip_content_gates=True,
            )
            await session.commit()
            assert result.skipped and result.reason == "company_already_applied"

    async def test_rejecting_it_lets_the_next_one_through(self, factory):
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a")
            _apply_row(session, first, PROFILE)
            await session.commit()
            # The apply is tied to this listing; rejecting it writes it off.
            first.status = REJECTED
            await session.commit()

            result = await process_listing(
                session,
                {
                    "company": "Acme LLC",
                    "title": "Staff Engineer",
                    "url": "https://example.com/b",
                    "location": "Remote, US",
                },
                persona_id=PROFILE,
                skip_content_gates=True,
            )
            await session.commit()
            assert result.ok, result.reason

    async def test_an_applied_row_is_written_off_too(self, factory):
        """The row's own applied status closes the company; rejecting reopens it."""
        async with factory() as session:
            job = await _add(session, "Acme", "https://example.com/a")
            job.status = JobStatus.APPLIED.value
            job.applied_at = utcnow()
            await session.commit()
            normalized = normalize_company("Acme")
            assert await company_is_applied(session, PROFILE, normalized)

            job.status = REJECTED
            await session.commit()
            assert not await company_is_applied(session, PROFILE, normalized)

    async def test_an_application_with_no_listing_still_counts(self, factory):
        """An Application that names no job is the only record of that apply."""
        async with factory() as session:
            session.add(
                Application(
                    persona_id=PROFILE,
                    company="Acme",
                    normalized_company=normalize_company("Acme"),
                    job_id=None,
                    status="applied",
                )
            )
            await session.commit()
            assert await company_is_applied(session, PROFILE, normalize_company("Acme"))

    async def test_an_apply_older_than_30_days_reopens_the_company(self, factory):
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a")
            await _age(session, first)  # clear the one-per-company window too
            _apply_row(session, first, PROFILE)
            await session.commit()
            old = utcnow() - timedelta(days=31)
            for app in (await session.execute(select(Application))).scalars():
                app.created_at = old
            await session.commit()

            result = await process_listing(
                session,
                {
                    "company": "Acme LLC",
                    "title": "Staff Engineer",
                    "url": "https://example.com/b",
                    "location": "Remote, US",
                },
                persona_id=PROFILE,
                skip_content_gates=True,
            )
            await session.commit()
            assert result.ok, result.reason

    async def test_another_applied_listing_still_holds_the_company(self, factory):
        """Rejecting one job does not clear an apply recorded by another (same persona)."""
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a")
            await _age(session, first)  # past the one-per-company window
            second = await _add(session, "Acme", "https://example.com/b", "Analytics Engineer")
            first.status = JobStatus.APPLIED.value
            first.applied_at = utcnow()
            second.status = JobStatus.APPLIED.value
            second.applied_at = utcnow()
            await session.commit()

            first.status = REJECTED
            await session.commit()
            assert await company_is_applied(session, PROFILE, normalize_company("Acme"))


class TestItStaysOnTheListButOutOfTheWork:
    async def test_the_listing_is_still_there(self, factory):
        async with factory() as session:
            job = await _add(session, "Acme", "https://example.com/a")
            job.status = REJECTED
            await session.commit()
            rows = (await session.execute(select(job_model(PROFILE)))).scalars().all()
            assert [r.status for r in rows] == [REJECTED]

    async def test_it_reads_as_rejected_even_with_a_resume(self, factory):
        """A row that carries a resume still reads as rejected — no re-derivation."""
        from app.api.jobs import _job_dict

        async with factory() as session:
            job = await _add(session, "Acme", "https://example.com/a")
            job.description = "A long enough description to count as a usable JD." * 3
            job.folder_path = "/some/where"
            job.ats_score = 88
            job.status = REJECTED
            await session.commit()
            assert _job_dict(job, profile=PROFILE)["status"] == REJECTED

    async def test_no_batch_picks_it_up(self, factory):
        """Deep scan and tailor select by status, and this is not one of them."""
        from app.services.bg_jobs import _load_jobs_for_bg

        async with factory() as session:
            keep = await _add(session, "Acme", "https://example.com/a")
            await _age(session, keep)
            drop = await _add(session, "Beta", "https://example.com/b")
            drop.status = REJECTED
            await session.commit()

            row = BackgroundJob(
                kind="deep_scan",
                status="running",
                persona_id=PROFILE,
                seq_from=1,
                seq_to=10**6,
            )
            session.add(row)
            await session.commit()

            picked = await _load_jobs_for_bg(
                session,
                row,
                statuses=[
                    JobStatus.NEW.value,
                    JobStatus.JD_READY.value,
                    JobStatus.SCAN_FAILED.value,
                ],
                persona_id=PROFILE,
            )
            assert [j.id for j in picked] == [keep.id]


BR = "Brazil, Remote"


class TestCountryRoutingAndBruno:
    """A Brazil job is Bruno's; a US job is the US personas'."""

    async def test_a_brazil_listing_goes_only_to_bruno(self, factory):
        async with factory() as session:
            results = await ingest_listing_for_personas(
                session,
                {
                    "company": "Acme",
                    "title": "Staff Engineer",
                    "url": "https://example.com/br",
                    "location": BR,
                },
                persona_ids=[PROFILE, "bruno_raphael"],
                skip_content_gates=True,
            )
            await session.commit()
            assert set(results) == {"bruno_raphael"}
            assert results["bruno_raphael"].ok

    async def test_a_us_listing_skips_bruno(self, factory):
        async with factory() as session:
            results = await ingest_listing_for_personas(
                session,
                {
                    "company": "Acme",
                    "title": "Staff Engineer",
                    "url": "https://example.com/us",
                    "location": "Remote, US",
                },
                persona_ids=[PROFILE, "bruno_raphael"],
                skip_content_gates=True,
            )
            await session.commit()
            assert set(results) == {PROFILE}
            assert results[PROFILE].ok

    async def test_bruno_applied_closes_a_brazil_company_for_bruno(self, factory):
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a", location=BR, pid="bruno_raphael")
            _apply_row(session, first, "bruno_raphael")
            await session.commit()

            result = await process_listing(
                session,
                {
                    "company": "Acme",
                    "title": "Staff Engineer",
                    "url": "https://example.com/b",
                    "location": BR,
                },
                persona_id="bruno_raphael",
                skip_content_gates=True,
            )
            await session.commit()
            assert result.skipped and result.reason == "company_already_applied"
