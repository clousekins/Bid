from __future__ import annotations

import pytest

from app.services.bg_jobs import _clamp_concurrency


def test_clamp_concurrency():
    assert _clamp_concurrency(5, default=5) == 5
    assert _clamp_concurrency(0, default=5) == 1
    assert _clamp_concurrency(100, default=5) == 32
    assert _clamp_concurrency("8", default=5) == 8  # type: ignore[arg-type]
    assert _clamp_concurrency("nope", default=5) == 5  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_parallel_tailor_progress_has_concurrency(tmp_path, monkeypatch):
    from sqlalchemy import select

    from app.config import get_settings
    from app.db import session as sess
    from app.db.enums import BackgroundJobKind, BackgroundJobStatus, JobStatus
    from app.db.models import BackgroundJob, Job
    from app.db.session import get_session_factory, init_db
    from app.services import bg_jobs
    from app.services.pipeline import process_listing

    db_file = tmp_path / "parallel.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    monkeypatch.setenv("TAILOR_CONCURRENCY", "3")
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()
    # enqueue() starts the real worker, which would race the executor called
    # by hand below and run the same jobs a second time.
    monkeypatch.setattr(bg_jobs, "ensure_worker", lambda: None)

    factory = get_session_factory()
    async with factory() as session:
        ids = []
        for i in range(1, 4):
            r = await process_listing(
                session,
                {
                    "company": f"Co{i}",
                    "title": "Senior Engineer",
                    "url": f"https://example.com/jobs/{i}",
                    "location": "Remote, US",
                    "description": "A" * 80,
                },
                skip_content_gates=True,
            )
            assert r.ok and r.job
            r.job.status = JobStatus.JD_READY.value
            ids.append(r.job.id)
        await session.commit()

    seen: list[int] = []

    async def fake_tailor(session, job, *, persona_id=None):
        seen.append(job.id)
        await asyncio.sleep(0.05)
        return {"job_id": job.id, "ok": True, "persona_id": persona_id or "phong_v_cao"}

    import asyncio

    monkeypatch.setattr("app.services.tailor.tailor_job", fake_tailor)

    row = await bg_jobs.enqueue(
        kind=BackgroundJobKind.TAILOR,
        persona_id="phong_v_cao",
        job_ids=ids,
        generate_as="andrew_nguyen",
    )
    assert row.id

    # Run executor directly (bypass worker loop)
    async with factory() as session:
        bg = await session.get(BackgroundJob, row.id)
        assert bg
        bg.status = BackgroundJobStatus.RUNNING.value
        await session.commit()
        await bg_jobs._exec_tailor(session, bg)

    async with factory() as session:
        bg = await session.get(BackgroundJob, row.id)
        assert bg
        assert bg.status == BackgroundJobStatus.COMPLETED.value
        import json

        prog = json.loads(bg.progress or "{}")
        assert prog["concurrency"] == 3
        assert prog["generate_as"] == "andrew_nguyen"
        assert prog["done"] == 3
        assert len(prog["items"]) == 3
        assert set(seen) == set(ids)
