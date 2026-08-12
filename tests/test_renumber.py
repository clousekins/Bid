from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session as sess
from app.db.models import Job
from app.db.session import get_session_factory, init_db
from app.services.folder_writer import ensure_job_folder, job_folder_name
from app.services.pipeline import process_listing
from app.services.renumber import renumber_all_jobs


@pytest.mark.asyncio
async def test_renumber_after_gap(tmp_path, monkeypatch):
    db_file = tmp_path / "renumber.db"
    out = tmp_path / "applications"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(out))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()

    factory = get_session_factory()
    async with factory() as session:
        jobs = []
        for i in range(1, 4):
            r = await process_listing(
                session,
                {
                    "company": f"Co{i}",
                    "title": "Senior Engineer",
                    "url": f"https://example.com/jobs/{i}",
                    "location": "Remote, US",
                },
                skip_content_gates=True,
            )
            assert r.ok
            jobs.append(r.job)
        await session.commit()

        # Create folders for phong under seq 1,2,3 then leave a gap by deleting seq 2 row only
        for j in jobs:
            ensure_job_folder("phong_v_cao", j.seq_no, j.company)
            ensure_job_folder("brian_gou", j.seq_no, j.company)
            j.folder_path = str(out / "phong_v_cao" / job_folder_name(j.seq_no, j.company))

        mid = jobs[1]
        mid_seq = mid.seq_no
        mid_company = mid.company
        await session.delete(mid)
        await session.commit()

        # Manually remove mid folders (simulate purge)
        from app.services.folder_writer import remove_all_persona_outputs

        remove_all_persona_outputs(
            seq_no=mid_seq,
            company=mid_company,
            persona_ids=["phong_v_cao", "brian_gou"],
        )

        remaining = list(
            (await session.execute(select(Job).order_by(Job.seq_no.asc()))).scalars().all()
        )
        assert [j.seq_no for j in remaining] == [1, 3]

        result = await renumber_all_jobs(session)
        await session.commit()
        assert result["changed"] is True
        assert result["seq_max"] == 2

        remaining = list(
            (await session.execute(select(Job).order_by(Job.seq_no.asc()))).scalars().all()
        )
        assert [j.seq_no for j in remaining] == [1, 2]
        assert (out / "phong_v_cao" / "1-Co1").is_dir()
        assert (out / "phong_v_cao" / "2-Co3").is_dir()
        assert (out / "brian_gou" / "2-Co3").is_dir()
        assert not (out / "phong_v_cao" / "3-Co3").exists()
