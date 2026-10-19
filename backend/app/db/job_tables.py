"""The per-persona ``jobs__{persona_id}`` tables and how to reach them.

Each persona owns a physical jobs table with the shared :class:`JobBase` column
set. ``job_model(pid)`` builds (once, cached) the mapped class for a persona;
``ensure_job_table`` creates the table on demand; ``resolve_job`` turns a global
job id back into (model, row) via the :class:`JobIndex` registry.

Why a cache is mandatory, not an optimization: calling ``type(...)`` twice for the
same ``__tablename__`` registers the table in ``Base.metadata`` twice and raises.
``_JOB_MODELS`` guarantees one class per persona for the life of the process.
"""
from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base, JobBase, JobIndex
from app.services.persona import (
    all_persona_ids,
    get_active_persona_id,
    persona_countries,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_]+$")

# persona_id -> mapped class. Also the metadata-registration guard (see module doc).
_JOB_MODELS: dict[str, type] = {}
# personas whose table we have already created this process.
_ENSURED: set[str] = set()


def job_model(persona_id: str | None) -> type[JobBase]:
    """The mapped class for this persona's jobs table, built once and cached."""
    pid = persona_id or get_active_persona_id()
    if not _SAFE_ID.match(pid):
        raise ValueError(f"unsafe persona id for a table name: {pid!r}")
    model = _JOB_MODELS.get(pid)
    if model is None:
        table = f"jobs__{pid}"
        model = type(
            f"Job_{pid}",
            (JobBase, Base),
            {"__tablename__": table, "__persona_id__": pid},
        )
        _JOB_MODELS[pid] = model
    return model


def prewarm_job_models() -> list[str]:
    """Register a mapped class for every persona in the config.

    Called at startup so all tables are in ``Base.metadata`` before
    ``create_all`` and before the migration runs. Returns the ids registered.
    """
    ids = all_persona_ids()
    for pid in ids:
        if _SAFE_ID.match(pid):
            job_model(pid)
    return ids


async def ensure_job_table(persona_id: str) -> type[JobBase]:
    """Create this persona's table if it does not exist yet, and return its model.

    Runs in its own short transaction so it never widens an ingest's write lock.
    ``CREATE TABLE IF NOT EXISTS`` + a per-process guard make concurrent callers
    safe under the WAL + busy_timeout already configured.
    """
    pid = persona_id or get_active_persona_id()
    model = job_model(pid)
    if pid in _ENSURED:
        return model
    from app.db.session import get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[model.__table__], checkfirst=True)
        )
    _ENSURED.add(pid)
    return model


async def resolve_job(session: AsyncSession, job_id: int):
    """(model, row) for a global job id, or None. Routes through JobIndex."""
    idx = await session.get(JobIndex, job_id)
    if idx is None:
        return None
    model = job_model(idx.persona_id)
    row = await session.get(model, job_id)
    if row is None:
        return None
    return model, row


async def allocate_job_id(
    session: AsyncSession, persona_id: str, normalized_company: str | None
) -> int:
    """Mint a globally-unique job id for a new per-persona row."""
    idx = JobIndex(persona_id=persona_id, normalized_company=normalized_company)
    session.add(idx)
    await session.flush()
    return int(idx.id)


def persona_accepts_country(persona_id: str, country: str | None) -> bool:
    """Whether a listing from this country is eligible for this persona's table."""
    return (country or "US").upper() in persona_countries(persona_id)
