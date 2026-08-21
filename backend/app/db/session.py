from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.enums import JobStatus
from app.db.models import Base, Job, JobPackage

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _configure_sqlite(dbapi_conn, _connection_record) -> None:
    """WAL + busy timeout: needed when bg workers and API share one SQLite file."""
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        connect_args: dict = {"check_same_thread": False}
        if "sqlite" in settings.sqlalchemy_url:
            # aiosqlite: seconds to wait on lock instead of raising immediately
            connect_args["timeout"] = 30.0
        _engine = create_async_engine(
            settings.sqlalchemy_url,
            echo=False,
            connect_args=connect_args,
            poolclass=NullPool,
        )
        if "sqlite" in settings.sqlalchemy_url:
            event.listen(_engine.sync_engine, "connect", _configure_sqlite)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def _backfill_packages(session: AsyncSession) -> int:
    """Create packages for legacy resume_ready jobs that have no package rows yet."""
    from sqlalchemy import select

    rows = (
        await session.execute(select(Job).where(Job.status == JobStatus.RESUME_READY.value))
    ).scalars().all()
    created = 0
    for job in rows:
        existing = (
            await session.execute(
                select(JobPackage).where(
                    JobPackage.job_id == job.id,
                    JobPackage.persona_id == job.persona_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            continue
        session.add(
            JobPackage(
                job_id=job.id,
                persona_id=job.persona_id or "phong_v_cao",
                status=JobStatus.RESUME_READY.value,
                folder_path=job.folder_path,
            )
        )
        created += 1
    if created:
        await session.commit()
    return created


async def _heal_legacy_listing_statuses(session: AsyncSession) -> int:
    """Move package-backed jobs back to the shared listing/JD lifecycle."""
    from sqlalchemy import case, func, update

    package_derived_states = [
        JobStatus.RESUME_READY.value,
        JobStatus.APPLYING.value,
        JobStatus.APPLIED.value,
    ]
    result = await session.execute(
        update(Job)
        .where(Job.status.in_(package_derived_states))
        .values(
            status=case(
                (
                    func.length(func.coalesce(Job.description, "")) >= 50,
                    JobStatus.JD_READY.value,
                ),
                else_=JobStatus.NEW.value,
            ),
        )
    )
    await session.commit()
    return int(result.rowcount or 0)


async def _heal_datetime_failed_packages(session: AsyncSession) -> int:
    """Resume files were written; only listing timestamp compare failed."""
    from sqlalchemy import update

    result = await session.execute(
        update(JobPackage)
        .where(
            JobPackage.status == "failed",
            JobPackage.notes.like("%offset-naive and offset-aware datetimes%"),
            JobPackage.folder_path.is_not(None),
        )
        .values(status="resume_ready", notes=None)
    )
    await session.execute(
        update(Job)
        .where(Job.notes.like("%offset-naive and offset-aware datetimes%"))
        .values(notes=None)
    )
    await session.commit()
    return int(result.rowcount or 0)


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE jobs SET status = 'scan_failed' "
                "WHERE status = 'new' AND notes LIKE 'deep_scan_failed:%'"
            )
        )
    factory = get_session_factory()
    async with factory() as session:
        await _backfill_packages(session)
        await _heal_legacy_listing_statuses(session)
        await _heal_datetime_failed_packages(session)
        from app.services.ats_catalog import seed_ats_catalog

        await seed_ats_catalog(session, demote_missing=True)
        await session.commit()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session
