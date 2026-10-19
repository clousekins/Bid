"""Rejecting a listing by hand.

A rejected job stays on the list and stays readable. What it stops doing is
taking part: no batch picks it up, and it no longer holds its company against
the next job that comes from there.

That last part is the whole point of the status, and it is not free — an apply
is recorded in three places, and two of them survive a change to the listing's
own status.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session as sess
from app.db.enums import JobStatus, PackageStatus, can_transition
from app.db.models import Application, BackgroundJob, Job, JobPackage, utcnow
from app.db.session import get_session_factory, init_db
from app.services.pipeline import APPLY_GROUP, company_is_applied, process_listing
from app.validators.dedupe import normalize_company

REJECTED = JobStatus.REJECTED.value


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    db_file = tmp_path / "rejected.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    # The apply-crew gate (skip once the whole crew has applied at a company) is
    # always on now, independent of ALLOW_COMPANY_MULTI_APPLY — these exercise
    # that gate and its reopening behaviour.
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()
    return get_session_factory()


async def _add(
    session, company: str, url: str, title: str = "Data Engineer", location: str = "Remote, US"
) -> Job:
    result = await process_listing(
        session,
        {"company": company, "title": title, "url": url, "location": location},
        skip_content_gates=True,
    )
    await session.commit()
    assert result.ok, result.reason
    return result.job


async def _age(session, job, days: int = 11) -> None:
    """Push a listing past the one-per-company window so a sibling can ingest."""
    job.created_at = utcnow() - timedelta(days=days)
    await session.commit()


class TestReachingTheStatus:
    """Rejecting overrules the pipeline, so it is reachable from anywhere."""

    @pytest.mark.parametrize(
        "current",
        [s for s in JobStatus if s is not JobStatus.REJECTED],
    )
    def test_any_status_can_be_rejected(self, current):
        assert can_transition(current, JobStatus.REJECTED)

    def test_including_the_ones_that_are_otherwise_terminal(self):
        """applied, blocked and rejected_onsite accept nothing else at all."""
        for terminal in (JobStatus.APPLIED, JobStatus.BLOCKED, JobStatus.REJECTED_ONSITE):
            assert not can_transition(terminal, JobStatus.JD_READY)
            assert can_transition(terminal, JobStatus.REJECTED)

    def test_it_can_be_undone(self):
        """A misplaced click should not need a database edit to fix."""
        assert can_transition(JobStatus.REJECTED, JobStatus.JD_READY)
        assert can_transition(JobStatus.REJECTED, JobStatus.NEW)

    def test_but_not_undone_straight_into_applied(self):
        assert not can_transition(JobStatus.REJECTED, JobStatus.APPLIED)


def _apply_whole_crew(session, job) -> None:
    """Record every crew profile as applied at this job's company."""
    normalized = normalize_company(job.company)
    for persona_id in APPLY_GROUP:
        session.add(
            Application(
                persona_id=persona_id,
                company=job.company,
                normalized_company=normalized,
                job_id=job.id,
                status="applied",
            )
        )


class TestTheCompanyGateReopens:
    """The three records of an apply, and a rejected listing against each."""

    async def test_a_partial_crew_still_lets_a_job_through(self, factory):
        """Once past the one-per-company window, the apply crew decides: with
        only part of the crew applied (Alex, not Brian), the next job is let in."""
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a")
            # Clear the one-per-company window so the apply-crew gate is what
            # the second ingest actually turns on.
            await _age(session, first)
            # Only one crew member has applied; the other has not, so the crew
            # is still incomplete and the company stays open to ingest.
            session.add(
                Application(
                    persona_id=APPLY_GROUP[0],
                    company=first.company,
                    normalized_company=normalize_company(first.company),
                    job_id=first.id,
                    status="applied",
                )
            )
            await session.commit()

            result = await process_listing(
                session,
                {
                    "company": "Acme LLC",
                    "title": "Staff Engineer",
                    "url": "https://example.com/b",
                    "location": "Remote, US",
                },
                skip_content_gates=True,
            )
            await session.commit()
            assert result.ok, result.reason

    async def test_the_whole_crew_applied_blocks_a_second_job(self, factory):
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a")
            _apply_whole_crew(session, first)
            await session.commit()

            result = await process_listing(
                session,
                {
                    "company": "Acme LLC",
                    "title": "Staff Engineer",
                    "url": "https://example.com/b",
                    "location": "Remote, US",
                },
                skip_content_gates=True,
            )
            await session.commit()
            assert result.skipped and result.reason == "company_already_applied"

    async def test_rejecting_it_lets_the_next_one_through(self, factory):
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a")
            _apply_whole_crew(session, first)
            await session.commit()

            # Every crew apply is tied to this listing, so rejecting it writes
            # them all off and reopens the company.
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
                skip_content_gates=True,
            )
            await session.commit()
            assert result.ok, result.reason

    async def test_an_application_row_tied_to_it_is_written_off_too(self, factory):
        """The listing's status is not the only record of an apply."""
        async with factory() as session:
            job = await _add(session, "Acme", "https://example.com/a")
            job.status = JobStatus.APPLIED.value
            session.add(
                Application(
                    persona_id=job.persona_id,
                    company=job.company,
                    normalized_company=normalize_company(job.company),
                    job_id=job.id,
                    status="applied",
                )
            )
            await session.commit()
            normalized = normalize_company("Acme")
            assert await company_is_applied(session, job.persona_id, normalized)

            job.status = REJECTED
            await session.commit()
            assert not await company_is_applied(session, job.persona_id, normalized)

    async def test_an_applied_package_is_written_off_too(self, factory):
        async with factory() as session:
            job = await _add(session, "Acme", "https://example.com/a")
            session.add(
                JobPackage(
                    job_id=job.id,
                    persona_id=job.persona_id,
                    status=PackageStatus.APPLIED.value,
                )
            )
            await session.commit()
            normalized = normalize_company("Acme")
            assert await company_is_applied(session, job.persona_id, normalized)

            job.status = REJECTED
            await session.commit()
            assert not await company_is_applied(session, job.persona_id, normalized)

    async def test_an_application_with_no_listing_still_counts(self, factory):
        """Rejecting a listing writes off that listing, not the company.

        An Application that names no job is the only record of that apply —
        there is nothing to reject, so it keeps the company closed.
        """
        async with factory() as session:
            job = await _add(session, "Acme", "https://example.com/a")
            session.add(
                Application(
                    persona_id=job.persona_id,
                    company="Acme",
                    normalized_company=normalize_company("Acme"),
                    job_id=None,
                    status="applied",
                )
            )
            job.status = REJECTED
            await session.commit()
            assert await company_is_applied(
                session, job.persona_id, normalize_company("Acme")
            )

    async def test_an_apply_older_than_30_days_reopens_the_company(self, factory):
        """Even the whole crew applied cannot close a company once the applies
        are older than the 30-day window — the next job ingests."""
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a")
            await _age(session, first)  # clear the one-per-company window
            _apply_whole_crew(session, first)
            await session.commit()

            # Age every crew apply past the 30-day window.
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
                skip_content_gates=True,
            )
            await session.commit()
            assert result.ok, result.reason

    async def test_another_applied_listing_still_holds_the_company(self, factory):
        """Rejecting one job does not clear an apply recorded by another."""
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a")
            await _age(session, first)  # past the one-per-company window
            second = await _add(session, "Acme", "https://example.com/b", "Analytics Engineer")
            first.status = JobStatus.APPLIED.value
            second.status = JobStatus.APPLIED.value
            await session.commit()

            first.status = REJECTED
            await session.commit()
            assert await company_is_applied(
                session, first.persona_id, normalize_company("Acme")
            )


class TestItStaysOnTheListButOutOfTheWork:
    async def test_the_listing_is_still_there(self, factory):
        async with factory() as session:
            job = await _add(session, "Acme", "https://example.com/a")
            job.status = REJECTED
            await session.commit()
            rows = (await session.execute(select(Job))).scalars().all()
            assert [r.status for r in rows] == [REJECTED]

    async def test_it_reads_as_rejected_even_with_a_package(self, factory):
        """A package would otherwise re-derive the listing as jd_ready."""
        from app.api.jobs import _listing_pipeline_status

        async with factory() as session:
            job = await _add(session, "Acme", "https://example.com/a")
            job.description = "A long enough description to count as a usable JD." * 3
            job.status = REJECTED
            session.add(
                JobPackage(
                    job_id=job.id,
                    persona_id=job.persona_id,
                    status=PackageStatus.RESUME_READY.value,
                )
            )
            await session.commit()
            assert _listing_pipeline_status(job) == REJECTED

    async def test_no_batch_picks_it_up(self, factory):
        """Deep scan and tailor select by status, and this is not one of them."""
        from app.services.bg_jobs import _load_jobs_for_bg

        async with factory() as session:
            keep = await _add(session, "Acme", "https://example.com/a")
            drop = await _add(session, "Beta", "https://example.com/b")
            drop.status = REJECTED
            await session.commit()

            row = BackgroundJob(
                kind="deep_scan",
                status="running",
                persona_id=keep.persona_id,
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
            )
            assert [j.id for j in picked] == [keep.id]


BR = "Brazil, Remote"


class TestBrazilListingsAreBrunos:
    """A Brazil job is Bruno's: it closes to ingest on Bruno, not the US pair."""

    async def test_bruno_applied_closes_a_brazil_company(self, factory):
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a", location=BR)
            session.add(
                Application(
                    persona_id="bruno_raphael",
                    company="Acme",
                    normalized_company=normalize_company("Acme"),
                    job_id=first.id,
                    status="applied",
                )
            )
            await session.commit()

            result = await process_listing(
                session,
                {
                    "company": "Acme",
                    "title": "Staff Engineer",
                    "url": "https://example.com/b",
                    "location": BR,
                },
                skip_content_gates=True,
            )
            await session.commit()
            assert result.skipped and result.reason == "company_already_applied"

    async def test_the_us_pair_does_not_close_a_brazil_company(self, factory):
        """Alex and Brian applying at a Brazil company does not shut Bruno out."""
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a", location=BR)
            _apply_whole_crew(session, first)  # alex + brian, not Bruno
            await session.commit()
            await _age(session, first)  # clear the one-per-company window

            result = await process_listing(
                session,
                {
                    "company": "Acme",
                    "title": "Staff Engineer",
                    "url": "https://example.com/b",
                    "location": BR,
                },
                skip_content_gates=True,
            )
            await session.commit()
            assert result.ok, result.reason

    async def test_bruno_does_not_close_a_us_company(self, factory):
        """Bruno applying at a US company does not shut the US pair out."""
        async with factory() as session:
            first = await _add(session, "Acme", "https://example.com/a")  # US
            session.add(
                Application(
                    persona_id="bruno_raphael",
                    company="Acme",
                    normalized_company=normalize_company("Acme"),
                    job_id=first.id,
                    status="applied",
                )
            )
            await session.commit()
            await _age(session, first)  # clear the one-per-company window

            result = await process_listing(
                session,
                {
                    "company": "Acme",
                    "title": "Staff Engineer",
                    "url": "https://example.com/b",
                    "location": "Remote, US",
                },
                skip_content_gates=True,
            )
            await session.commit()
            assert result.ok, result.reason
