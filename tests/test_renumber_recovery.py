"""Recovery for a renumber that died between its two rename phases.

renumber_all_jobs moves every folder to a __renum_ temp name before moving any
to its final name. An interruption in phase B stranded 1,444 folders under temp
names, and the API still reported success, so nothing ever repaired them.
"""
from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.db import session as sess
from app.db.models import Job, JobPackage
from app.db.session import get_session_factory, init_db
from app.services.renumber import heal_stuck_renumber_folders


async def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()
    return get_settings().output_root


async def _job(seq_no: int, company: str, persona: str = "brian_gou") -> int:
    factory = get_session_factory()
    async with factory() as db:
        job = Job(
            seq_no=seq_no,
            company=company,
            normalized_company=company.lower(),
            title="Data Engineer",
            url=f"https://example.com/{seq_no}",
            persona_id=persona,
        )
        db.add(job)
        await db.flush()
        db.add(JobPackage(job_id=job.id, persona_id=persona, status="applied"))
        await db.commit()
        return job.id


@pytest.mark.asyncio
async def test_stranded_folders_are_renamed_to_their_real_names(tmp_path, monkeypatch):
    root = await _setup(tmp_path, monkeypatch)
    job_id = await _job(965, "United IT Solutions")

    stuck = root / "brian_gou" / f"__renum_{job_id}__United-IT-Solutions"
    stuck.mkdir(parents=True)
    (stuck / "Brian Gou.pdf").write_text("resume")
    (stuck / "meta.json").write_text(json.dumps({"seq_no": 1000, "job_id": job_id}))

    factory = get_session_factory()
    async with factory() as db:
        result = await heal_stuck_renumber_folders(db)

    assert result["healed"] == 1
    assert not stuck.exists()
    fixed = root / "brian_gou" / "965-United-IT-Solutions"
    assert fixed.is_dir(), sorted(p.name for p in (root / "brian_gou").iterdir())
    assert (fixed / "Brian Gou.pdf").read_text() == "resume"
    # meta.json is corrected to the real seq
    assert json.loads((fixed / "meta.json").read_text())["seq_no"] == 965


@pytest.mark.asyncio
async def test_db_paths_follow_the_recovered_folder(tmp_path, monkeypatch):
    root = await _setup(tmp_path, monkeypatch)
    job_id = await _job(12, "Acme Corp")
    stuck = root / "brian_gou" / f"__renum_{job_id}__Acme-Corp"
    stuck.mkdir(parents=True)

    factory = get_session_factory()
    async with factory() as db:
        await heal_stuck_renumber_folders(db)
    async with factory() as db:
        job = await db.get(Job, job_id)
        from sqlalchemy import select

        pkg = (await db.execute(select(JobPackage))).scalars().one()
        assert job.folder_path is not None
        assert "__renum_" not in job.folder_path
        assert "__renum_" not in (pkg.folder_path or "")
        assert pkg.folder_path == job.folder_path


@pytest.mark.asyncio
async def test_a_collision_is_reported_not_silently_swallowed(tmp_path, monkeypatch):
    """The bug was calling a failed rename a success. Blocked must be counted."""
    root = await _setup(tmp_path, monkeypatch)
    job_id = await _job(7, "Beta")
    persona_dir = root / "brian_gou"
    stuck = persona_dir / f"__renum_{job_id}__Beta"
    stuck.mkdir(parents=True)
    (persona_dir / "7-Beta").mkdir()  # destination already taken

    factory = get_session_factory()
    async with factory() as db:
        result = await heal_stuck_renumber_folders(db)

    assert result["healed"] == 0
    assert result["blocked"] == 1
    assert stuck.exists(), "a blocked folder must be left intact, not destroyed"


@pytest.mark.asyncio
async def test_folder_for_a_deleted_job_is_left_alone(tmp_path, monkeypatch):
    root = await _setup(tmp_path, monkeypatch)
    stuck = root / "brian_gou" / "__renum_999999__Ghost"
    stuck.mkdir(parents=True)

    factory = get_session_factory()
    async with factory() as db:
        result = await heal_stuck_renumber_folders(db)

    assert result["orphaned"] == 1
    assert result["healed"] == 0
    assert stuck.exists()


@pytest.mark.asyncio
async def test_healthy_folders_are_untouched(tmp_path, monkeypatch):
    root = await _setup(tmp_path, monkeypatch)
    await _job(3, "Fine Corp")
    ok = root / "brian_gou" / "3-Fine-Corp"
    ok.mkdir(parents=True)

    factory = get_session_factory()
    async with factory() as db:
        result = await heal_stuck_renumber_folders(db)

    assert result == {"healed": 0, "blocked": 0, "orphaned": 0}
    assert ok.is_dir()


@pytest.mark.asyncio
async def test_recovery_runs_on_startup(tmp_path, monkeypatch):
    """It must repair itself without anyone invoking a script."""
    root = await _setup(tmp_path, monkeypatch)
    job_id = await _job(42, "Startup Heal")
    stuck = root / "brian_gou" / f"__renum_{job_id}__Startup-Heal"
    stuck.mkdir(parents=True)

    await init_db()  # a normal boot

    assert not stuck.exists()
    assert (root / "brian_gou" / "42-Startup-Heal").is_dir()


@pytest.mark.asyncio
async def test_recovery_handles_multiple_personas_for_one_job(tmp_path, monkeypatch):
    root = await _setup(tmp_path, monkeypatch)
    job_id = await _job(88, "Shared Co")
    for persona in ("brian_gou", "bing_lin"):
        (root / persona / f"__renum_{job_id}__Shared-Co").mkdir(parents=True)

    factory = get_session_factory()
    async with factory() as db:
        result = await heal_stuck_renumber_folders(db)

    assert result["healed"] == 2
    for persona in ("brian_gou", "bing_lin"):
        assert (root / persona / "88-Shared-Co").is_dir()
