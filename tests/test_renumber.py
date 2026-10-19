from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import session as sess
from app.db.job_tables import job_model
from app.db.session import get_session_factory, init_db
from app.services.folder_writer import remove_all_persona_outputs
from app.services.pipeline import process_listing
from app.services.renumber import renumber_all_jobs

# Each persona now owns its own jobs__{pid} table, numbered 1..N independently.
# Ingest the same listings into two of them so renumber_all_jobs has more than
# one table to fix and the per-persona folder renames can be checked on both.
PERSONAS = ("phong_v_cao", "brian_gou")


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
        # Ingest three companies into each persona's own table. process_listing
        # mints the seq_no (1,2,3 per table) and writes the resume folder under
        # output_root/{persona}/{seq}-{company}.
        for i in range(1, 4):
            for pid in PERSONAS:
                r = await process_listing(
                    session,
                    {
                        "company": f"Co{i}",
                        "title": "Senior Engineer",
                        "url": f"https://example.com/jobs/{i}",
                        "location": "Remote, US",
                    },
                    persona_id=pid,
                    skip_content_gates=True,
                )
                assert r.ok
        await session.commit()

        # Leave a gap in every persona's table: drop the seq-2 row (Co2) so each
        # table is left numbered [1, 3].
        for pid in PERSONAS:
            model = job_model(pid)
            mid = (
                await session.execute(select(model).where(model.seq_no == 2))
            ).scalar_one()
            await session.delete(mid)
        await session.commit()

        # Manually remove the Co2 folders under every persona (simulate purge).
        remove_all_persona_outputs(
            seq_no=2,
            company="Co2",
            persona_ids=list(PERSONAS),
        )

        phong = job_model("phong_v_cao")
        remaining = list(
            (await session.execute(select(phong).order_by(phong.seq_no.asc()))).scalars().all()
        )
        assert [j.seq_no for j in remaining] == [1, 3]

        result = await renumber_all_jobs(session)
        await session.commit()
        assert result["changed"] is True
        # Each persona table is renumbered to 1..2, so the aggregate max seq is 2.
        assert result["seq_max"] == 2

        remaining = list(
            (await session.execute(select(phong).order_by(phong.seq_no.asc()))).scalars().all()
        )
        assert [j.seq_no for j in remaining] == [1, 2]
        assert (out / "phong_v_cao" / "1-Co1").is_dir()
        assert (out / "phong_v_cao" / "2-Co3").is_dir()
        assert (out / "brian_gou" / "2-Co3").is_dir()
        assert not (out / "phong_v_cao" / "3-Co3").exists()
