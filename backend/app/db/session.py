from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Base

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
        # The created-table cache is per-engine; a fresh engine (tests reset it)
        # must forget what an earlier engine had created.
        from app.db import job_tables

        job_tables._ENSURED.clear()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def _backfill_country(conn) -> None:
    """Give legacy `jobs` rows a country, read from the location they already have.

    Runs once on the legacy table immediately after the column is added and
    before the per-persona migration copies the rows.
    """
    from sqlalchemy import text

    from app.services.job_country import DEFAULT, country_code

    rows = conn.execute(text("SELECT id, location FROM jobs")).fetchall()
    for row_id, location in rows:
        code = country_code(location)
        if code == DEFAULT:
            continue
        conn.execute(
            text("UPDATE jobs SET country = :code WHERE id = :id"),
            {"code": code, "id": row_id},
        )


def _backfill_salary(conn) -> None:
    """Read a pay range out of the JD every legacy `jobs` row already holds."""
    from sqlalchemy import text

    from app.services.salary_extract import extract_salary

    rows = conn.execute(
        text("SELECT id, description FROM jobs WHERE description IS NOT NULL")
    ).fetchall()
    for row_id, description in rows:
        salary = extract_salary(description)
        if not salary:
            continue
        conn.execute(
            text("UPDATE jobs SET salary = :salary WHERE id = :id"),
            {"salary": salary, "id": row_id},
        )


def _ensure_sqlite_columns(conn) -> None:
    """Add columns the legacy `jobs`/`job_packages` tables may be missing.

    Runs only while the old single-table schema is still present, so the
    per-persona migration copies complete rows. A no-op once migrated (the old
    `jobs` table has been renamed away).
    """
    from sqlalchemy import text

    tables = {
        str(r[0])
        for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "jobs" not in tables:
        return
    cols = {str(r[1]) for r in conn.execute(text("PRAGMA table_info(jobs)")).fetchall()}
    if "domains" not in cols:
        conn.execute(text("ALTER TABLE jobs ADD COLUMN domains VARCHAR(64)"))
    if "feasibility_score" not in cols:
        conn.execute(text("ALTER TABLE jobs ADD COLUMN feasibility_score INTEGER"))
    if "salary" not in cols:
        conn.execute(text("ALTER TABLE jobs ADD COLUMN salary VARCHAR(128)"))
        _backfill_salary(conn)
    if "country" not in cols:
        conn.execute(
            text("ALTER TABLE jobs ADD COLUMN country VARCHAR(2) NOT NULL DEFAULT 'US'")
        )
        _backfill_country(conn)

    if "job_packages" not in tables:
        return
    pkg_cols = {
        str(r[1])
        for r in conn.execute(text("PRAGMA table_info(job_packages)")).fetchall()
    }
    for name, ddl in (
        ("applied_at", "ALTER TABLE job_packages ADD COLUMN applied_at DATETIME"),
        (
            "outcome",
            "ALTER TABLE job_packages ADD COLUMN outcome VARCHAR(32) "
            "NOT NULL DEFAULT 'pending'",
        ),
        ("outcome_at", "ALTER TABLE job_packages ADD COLUMN outcome_at DATETIME"),
        ("outcome_notes", "ALTER TABLE job_packages ADD COLUMN outcome_notes TEXT"),
        ("ats_score", "ALTER TABLE job_packages ADD COLUMN ats_score INTEGER"),
        ("ats_breakdown", "ALTER TABLE job_packages ADD COLUMN ats_breakdown TEXT"),
        ("credibility_score", "ALTER TABLE job_packages ADD COLUMN credibility_score INTEGER"),
        ("credibility_findings", "ALTER TABLE job_packages ADD COLUMN credibility_findings TEXT"),
    ):
        if name not in pkg_cols:
            conn.execute(text(ddl))
    if "applied_at" not in pkg_cols:
        conn.execute(
            text(
                "UPDATE job_packages SET applied_at = updated_at "
                "WHERE status = 'applied' AND applied_at IS NULL"
            )
        )


# Old per-persona package status -> the folded single row status.
_PKG_STATUS_MAP = {
    "resume_ready": "resume_ready",
    "applying": "applying",
    "applied": "applied",
    "failed": "tailor_failed",
}
# Folded columns, in insert order (minus id/seq_no/persona which are handled apart).
_FOLD_COLS = [
    "company", "normalized_company", "company_id", "title", "url", "apply_url",
    "location", "country", "source", "external_id", "description", "salary",
    "status", "folder_path", "staffing_firm", "domains", "jd_fetched",
    "feasibility_score", "notes", "applied_at", "outcome", "outcome_at",
    "outcome_notes", "ats_score", "ats_breakdown", "credibility_score",
    "credibility_findings", "enabled", "created_at", "updated_at",
]


def _migrate_to_per_persona(conn) -> None:
    """One-time: split the shared `jobs` table into per-persona `jobs__{id}` tables.

    Idempotent and self-detecting: runs only when the old `jobs` table exists and
    `job_index` does not; archives the old tables by rename at the end so the
    marker flips. Runs inside init_db's single transaction, so a crash rolls back
    whole and never leaves it half-done. Keeps each row's original global seq_no
    (the on-disk folders are named by it) — a later per-persona Renumber tidies.
    """
    from collections import defaultdict

    from sqlalchemy import text

    from app.db.job_tables import job_model
    from app.db.models import JobIndex
    from app.services.persona import all_persona_ids

    tables = {
        str(r[0])
        for r in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    }
    if "jobs" not in tables or "job_index" in tables:
        return  # fresh DB, or already migrated

    jobs = [dict(r) for r in conn.execute(text("SELECT * FROM jobs")).mappings().all()]
    has_packages = "job_packages" in tables
    has_disables = "job_disables" in tables
    pkgs = (
        [dict(r) for r in conn.execute(text("SELECT * FROM job_packages")).mappings().all()]
        if has_packages
        else []
    )
    dis_rows = (
        conn.execute(text("SELECT job_id, persona_id, disabled FROM job_disables")).all()
        if has_disables
        else []
    )
    disabled_by = {(int(d[0]), str(d[1])): bool(d[2]) for d in dis_rows}

    pkgs_by_job: dict[int, list[dict]] = defaultdict(list)
    for p in pkgs:
        pkgs_by_job[int(p["job_id"])].append(p)

    # Every persona that needs a table: config plus any referenced by legacy data.
    pids = set(all_persona_ids())
    for j in jobs:
        pids.add(str(j.get("persona_id") or "phong_v_cao"))
    for p in pkgs:
        pids.add(str(p["persona_id"]))
    for (_jid, pid) in disabled_by:
        pids.add(pid)

    import re as _re

    pids = {p for p in pids if _re.match(r"^[A-Za-z0-9_]+$", p)}
    new_tables = [JobIndex.__table__] + [job_model(p).__table__ for p in sorted(pids)]
    Base.metadata.create_all(conn, tables=new_tables, checkfirst=True)

    def enabled_from_disable(key):
        d = disabled_by.get(key)
        if d is None:
            return None
        return False if d else True  # disabled=True -> off; disabled=False -> forced on

    def mint_id(persona: str, normcomp) -> int:
        res = conn.execute(
            text(
                "INSERT INTO job_index (persona_id, normalized_company, created_at) "
                "VALUES (:p, :c, CURRENT_TIMESTAMP)"
            ),
            {"p": persona, "c": normcomp},
        )
        return int(res.lastrowid)

    def insert_row(persona: str, new_id: int, seq_no, values: dict) -> None:
        cols = ["id", "seq_no", *_FOLD_COLS]
        placeholders = ", ".join(f":{c}" for c in cols)
        params = {"id": new_id, "seq_no": seq_no, **{c: values.get(c) for c in _FOLD_COLS}}
        conn.execute(
            text(f'INSERT INTO "jobs__{persona}" ({", ".join(cols)}) VALUES ({placeholders})'),
            params,
        )

    newid_map: dict[tuple[int, str], int] = {}

    for j in jobs:
        old_id = int(j["id"])
        normcomp = j.get("normalized_company")
        desc = j.get("description") or ""
        solid = len(desc) >= 50
        base = {c: j.get(c) for c in _FOLD_COLS if c in j}
        base.setdefault("outcome", "pending")
        base.setdefault("country", j.get("country") or "US")

        job_pkgs = pkgs_by_job.get(old_id, [])
        if job_pkgs:
            for p in job_pkgs:
                persona = str(p["persona_id"])
                if persona not in pids:
                    continue
                vals = dict(base)
                vals["status"] = _PKG_STATUS_MAP.get(p.get("status"), "jd_ready")
                vals["folder_path"] = p.get("folder_path") or j.get("folder_path")
                vals["notes"] = p.get("notes") or j.get("notes")
                for c in (
                    "applied_at", "outcome", "outcome_at", "outcome_notes",
                    "ats_score", "ats_breakdown", "credibility_score",
                    "credibility_findings",
                ):
                    vals[c] = p.get(c) if p.get(c) is not None else (
                        "pending" if c == "outcome" else None
                    )
                vals["enabled"] = enabled_from_disable((old_id, persona))
                new_id = mint_id(persona, normcomp)
                insert_row(persona, new_id, j.get("seq_no"), vals)
                newid_map[(old_id, persona)] = new_id
        else:
            persona = str(j.get("persona_id") or "phong_v_cao")
            if persona not in pids:
                persona = "phong_v_cao"
                pids.add(persona)
                Base.metadata.create_all(
                    conn, tables=[job_model(persona).__table__], checkfirst=True
                )
            vals = dict(base)
            status = j.get("status")
            # No package for this persona: an old shared resume/apply status was
            # never really this persona's, so walk it back to the JD lifecycle.
            if status in ("resume_ready", "applying", "applied"):
                status = "jd_ready" if solid else "new"
            if status == "new" and str(j.get("notes") or "").startswith("deep_scan_failed:"):
                status = "scan_failed"
            vals["status"] = status
            vals["enabled"] = enabled_from_disable((old_id, persona))
            new_id = mint_id(persona, normcomp)
            insert_row(persona, new_id, j.get("seq_no"), vals)
            newid_map[(old_id, persona)] = new_id

    # Repoint Application.job_id at the copy in its own persona's table.
    if "applications" in tables:
        apps = conn.execute(text("SELECT id, persona_id, job_id FROM applications")).all()
        for app_id, persona_id, job_id in apps:
            if job_id is None:
                continue
            new = newid_map.get((int(job_id), str(persona_id)))
            conn.execute(
                text("UPDATE applications SET job_id = :n WHERE id = :i"),
                {"n": new, "i": app_id},
            )

    conn.execute(text("ALTER TABLE jobs RENAME TO jobs_legacy"))
    if has_packages:
        conn.execute(text("ALTER TABLE job_packages RENAME TO job_packages_legacy"))
    if has_disables:
        conn.execute(text("ALTER TABLE job_disables RENAME TO job_disables_legacy"))


async def init_db() -> None:
    from app.db.job_tables import prewarm_job_models

    prewarm_job_models()  # register every persona's table before create_all
    engine = get_engine()
    async with engine.begin() as conn:
        if "sqlite" in str(engine.url):
            await conn.run_sync(_ensure_sqlite_columns)
            await conn.run_sync(_migrate_to_per_persona)
        await conn.run_sync(Base.metadata.create_all)

    factory = get_session_factory()
    async with factory() as session:
        from app.services.renumber import heal_stuck_renumber_folders

        healed = await heal_stuck_renumber_folders(session)
        if healed["healed"] or healed["blocked"]:
            import logging

            logging.getLogger("poong.renumber").info(
                "recovered interrupted renumber: %s", healed
            )
        from app.services.ats_catalog import seed_ats_catalog

        await seed_ats_catalog(session, demote_missing=True)
        await session.commit()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session
