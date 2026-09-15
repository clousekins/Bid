from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db.enums import BackgroundJobKind, BackgroundJobStatus, JobStatus, PackageStatus
from app.db.models import BackgroundJob, Job, JobPackage
from app.db.session import get_session_factory
from app.services.deep_scan import deep_scan_job

log = logging.getLogger("poong.bg")

_worker_task: asyncio.Task | None = None
_wake: asyncio.Event | None = None
_locks: dict[str, asyncio.Lock] | None = None
_progress_lock: asyncio.Lock | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_wake() -> asyncio.Event:
    global _wake
    if _wake is None:
        _wake = asyncio.Event()
    return _wake


def _get_progress_lock() -> asyncio.Lock:
    global _progress_lock
    if _progress_lock is None:
        _progress_lock = asyncio.Lock()
    return _progress_lock


def _get_locks() -> dict[str, asyncio.Lock]:
    global _locks
    if _locks is None:
        _locks = {
            BackgroundJobKind.DEEP_SCAN.value: asyncio.Lock(),
            BackgroundJobKind.TAILOR.value: asyncio.Lock(),
            BackgroundJobKind.DISCOVER.value: asyncio.Lock(),
            BackgroundJobKind.DISCOVER_COMPANIES.value: asyncio.Lock(),
            BackgroundJobKind.DISCOVER_FRESH.value: asyncio.Lock(),
            BackgroundJobKind.DISCOVER_SITE.value: asyncio.Lock(),
        }
    return _locks


async def _safe_write_progress(bg_id: int, progress: dict[str, Any]) -> None:
    """Update background_jobs.progress without failing the parent job on SQLite locks."""
    factory = get_session_factory()
    payload = json.dumps(progress)
    last_exc: Exception | None = None
    async with _get_progress_lock():
        for attempt in range(6):
            try:
                async with factory() as session:
                    row = await session.get(BackgroundJob, bg_id)
                    if not row:
                        return
                    row.progress = payload
                    row.updated_at = _utcnow()
                    await session.commit()
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                msg = str(exc).lower()
                if "locked" not in msg and "busy" not in msg:
                    log.warning("bg progress write failed job=%s: %s", bg_id, exc)
                    return
                await asyncio.sleep(0.15 * (attempt + 1))
    if last_exc:
        log.warning("bg progress give up job=%s: %s", bg_id, last_exc)


def _clamp_concurrency(value: int, *, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(1, min(32, n))


async def enqueue(
    *,
    kind: BackgroundJobKind | str,
    persona_id: str,
    seq_from: int | None = None,
    seq_to: int | None = None,
    job_ids: list[int] | None = None,
    generate_as: str | None = None,
    force: bool = False,
    options: dict[str, Any] | None = None,
) -> BackgroundJob:
    factory = get_session_factory()
    ids = sorted({int(i) for i in (job_ids or []) if i is not None})
    progress: dict[str, Any] = {"done": 0, "total": 0, "items": []}
    if ids:
        progress["job_ids"] = ids
    if generate_as:
        progress["generate_as"] = generate_as
    if force:
        progress["force"] = True
    if options:
        progress["options"] = options
        progress.update({k: v for k, v in options.items() if k in {"site", "window", "max_jobs", "domains", "roles"}})
    async with factory() as session:
        row = BackgroundJob(
            kind=str(kind.value if isinstance(kind, BackgroundJobKind) else kind),
            status=BackgroundJobStatus.QUEUED.value,
            persona_id=persona_id,
            seq_from=seq_from,
            seq_to=seq_to,
            progress=json.dumps(progress),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        job_id = row.id
    _get_wake().set()
    ensure_worker()
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(BackgroundJob, job_id)  # type: ignore[return-value]


def _progress_job_ids(row: BackgroundJob) -> list[int] | None:
    try:
        data = json.loads(row.progress or "{}")
    except json.JSONDecodeError:
        return None
    ids = data.get("job_ids")
    if not isinstance(ids, list) or not ids:
        return None
    return [int(i) for i in ids]


async def _load_jobs_for_bg(
    session,
    row: BackgroundJob,
    *,
    statuses: list[str],
    match_listing_persona: bool = True,
) -> list[Job]:
    ids = _progress_job_ids(row)
    if ids is not None:
        # Selected-ID batches ignore Job.persona_id — generation persona comes from progress
        q = (
            select(Job)
            .where(
                Job.id.in_(ids),
                Job.status.in_(statuses),
            )
            .order_by(Job.seq_no.asc())
        )
        return list((await session.execute(q)).scalars().all())
    clauses = [
        Job.seq_no >= (row.seq_from or 0),
        Job.seq_no <= (row.seq_to or 10**9),
        Job.status.in_(statuses),
    ]
    # Range tailor is shared across personas — do not require Job.persona_id == active
    if match_listing_persona:
        clauses.insert(0, Job.persona_id == row.persona_id)
    q = select(Job).where(*clauses).order_by(Job.seq_no.asc())
    return list((await session.execute(q)).scalars().all())


async def _filter_jobs_needing_persona_package(
    session,
    jobs: list[Job],
    *,
    persona_id: str,
) -> list[Job]:
    """Keep jobs that still need a resume package for this persona.

    Skip only when THAT persona already has resume_ready / applying / applied.
    Jobs Applied by another persona remain eligible.
    """
    if not jobs or not persona_id:
        return jobs
    job_ids = [j.id for j in jobs]
    rows = (
        await session.execute(
            select(JobPackage.job_id, JobPackage.status).where(
                JobPackage.job_id.in_(job_ids),
                JobPackage.persona_id == persona_id,
            )
        )
    ).all()
    done_ids = {
        jid
        for jid, st in rows
        if st
        in (
            PackageStatus.RESUME_READY.value,
            PackageStatus.APPLYING.value,
            PackageStatus.APPLIED.value,
        )
    }
    return [j for j in jobs if j.id not in done_ids]


def _force_regenerate(row: BackgroundJob) -> bool:
    """True when this run must retailor jobs that already have a package."""
    try:
        data = json.loads(row.progress or "{}")
    except json.JSONDecodeError:
        return False
    return bool(data.get("force"))


def _generate_persona_id(row: BackgroundJob) -> str | None:
    try:
        data = json.loads(row.progress or "{}")
    except json.JSONDecodeError:
        return None
    pid = data.get("generate_as") or data.get("persona_id")
    return str(pid) if pid else None


async def request_cancel(job_id: int) -> bool:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(BackgroundJob, job_id)
        if not row:
            return False
        row.cancel_requested = True
        if row.status == BackgroundJobStatus.QUEUED.value:
            row.status = BackgroundJobStatus.CANCELLED.value
        await session.commit()
        return True


async def heal_stuck_jobs(*, older_than_minutes: int = 2) -> int:
    """Mark orphaned RUNNING jobs cancelled after restart / hung workers."""
    from datetime import timedelta

    factory = get_session_factory()
    cutoff = _utcnow() - timedelta(minutes=max(1, older_than_minutes))
    async with factory() as session:
        rows = (
            await session.execute(
                select(BackgroundJob).where(
                    BackgroundJob.status == BackgroundJobStatus.RUNNING.value,
                    BackgroundJob.updated_at < cutoff,
                )
            )
        ).scalars().all()
        n = 0
        for row in rows:
            row.status = BackgroundJobStatus.CANCELLED.value
            row.cancel_requested = True
            row.error = (row.error or "healed_stuck_running")[:2000]
            row.updated_at = _utcnow()
            n += 1
        if n:
            await session.commit()
            log.warning("healed %s stuck RUNNING background jobs", n)
        return n


async def get_job(job_id: int) -> BackgroundJob | None:
    factory = get_session_factory()
    async with factory() as session:
        return await session.get(BackgroundJob, job_id)


def ensure_worker() -> None:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop(), name="poong-bg-worker")


async def _worker_loop() -> None:
    wake = _get_wake()
    running: set[asyncio.Task] = set()
    while True:
        try:
            # Reap finished tasks
            done = {t for t in running if t.done()}
            for t in done:
                try:
                    t.result()
                except Exception:  # noqa: BLE001
                    log.exception("bg worker task failed")
            running -= done

            # Start one job per free kind lock (best practice: overlap discover + deep_scan)
            while True:
                picked = await _pick_next()
                if not picked:
                    break
                task = asyncio.create_task(_run_job(picked), name=f"bg-{picked}")
                running.add(task)
                # Small yield so lock state updates before next pick
                await asyncio.sleep(0)

            if not running:
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=2.0)
                except TimeoutError:
                    pass
            else:
                await asyncio.wait(running, timeout=1.0, return_when=asyncio.FIRST_COMPLETED)
        except Exception:  # noqa: BLE001
            await asyncio.sleep(1.0)


async def _pick_next() -> int | None:
    locks = _get_locks()
    factory = get_session_factory()
    async with factory() as session:
        for kind in (
            BackgroundJobKind.DEEP_SCAN.value,
            BackgroundJobKind.TAILOR.value,
            BackgroundJobKind.DISCOVER.value,
            BackgroundJobKind.DISCOVER_FRESH.value,
            BackgroundJobKind.DISCOVER_SITE.value,
            BackgroundJobKind.DISCOVER_COMPANIES.value,
        ):
            lock = locks[kind]
            if lock.locked():
                continue
            q = (
                select(BackgroundJob)
                .where(
                    BackgroundJob.kind == kind,
                    BackgroundJob.status == BackgroundJobStatus.QUEUED.value,
                )
                .order_by(BackgroundJob.id.asc())
                .limit(1)
            )
            row = (await session.execute(q)).scalar_one_or_none()
            if row:
                # Claim immediately so parallel picks cannot double-start the same kind
                row.status = BackgroundJobStatus.RUNNING.value
                row.updated_at = _utcnow()
                await session.commit()
                return row.id
    return None


async def _run_job(job_id: int) -> None:
    locks = _get_locks()
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(BackgroundJob, job_id)
        if not row or row.status not in {
            BackgroundJobStatus.QUEUED.value,
            BackgroundJobStatus.RUNNING.value,
        }:
            return
        kind = row.kind
        if kind not in locks:
            row.status = BackgroundJobStatus.FAILED.value
            row.error = f"unknown kind {kind}"
            await session.commit()
            return

    async with locks[kind]:
        async with factory() as session:
            row = await session.get(BackgroundJob, job_id)
            if not row:
                return
            if row.status == BackgroundJobStatus.QUEUED.value:
                row.status = BackgroundJobStatus.RUNNING.value
                row.updated_at = _utcnow()
                await session.commit()
            elif row.status != BackgroundJobStatus.RUNNING.value:
                return
            kind_name = row.kind

        try:
            async with factory() as session:
                row = await session.get(BackgroundJob, job_id)
                if not row:
                    return
                if kind_name == BackgroundJobKind.DEEP_SCAN.value:
                    await _exec_deep_scan(session, row)
                elif kind_name == BackgroundJobKind.TAILOR.value:
                    await _exec_tailor(session, row)
                elif kind_name == BackgroundJobKind.DISCOVER.value:
                    await _exec_discover(session, row, mode="standard")
                elif kind_name == BackgroundJobKind.DISCOVER_FRESH.value:
                    await _exec_discover(session, row, mode="fresh")
                elif kind_name == BackgroundJobKind.DISCOVER_SITE.value:
                    await _exec_discover(session, row, mode="site")
                elif kind_name == BackgroundJobKind.DISCOVER_COMPANIES.value:
                    await _exec_discover_companies(session, row)
                else:
                    row.status = BackgroundJobStatus.FAILED.value
                    row.error = f"unknown kind {kind_name}"
                    await session.commit()
        except Exception as exc:  # noqa: BLE001
            log.exception("bg job %s failed", job_id)
            async with factory() as session:
                row = await session.get(BackgroundJob, job_id)
                if not row:
                    return
                row.status = BackgroundJobStatus.FAILED.value
                row.error = str(exc)[:2000]
                row.updated_at = _utcnow()
                await session.commit()


# How many fetched jobs the UI keeps in the progress payload. Every poll
# carries these, so this trades bytes for how much of a run stays visible.
DISCOVER_ITEMS_SHOWN = 400


async def _bg_cancelled(bg_id: int) -> bool:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(BackgroundJob, bg_id)
        return bool(row and row.cancel_requested)


async def _record_progress(
    *,
    bg_id: int,
    progress: dict[str, Any],
    items: list[dict[str, Any]],
    result: dict[str, Any],
    lock: asyncio.Lock,
) -> None:
    async with lock:
        items.append(result)
        progress["done"] = len(items)
        progress["items"] = items[-50:]
        factory = get_session_factory()
        async with factory() as session:
            row = await session.get(BackgroundJob, bg_id)
            if not row:
                return
            row.progress = json.dumps(progress)
            row.updated_at = _utcnow()
            await session.commit()


async def _exec_deep_scan(session, row: BackgroundJob) -> None:
    settings = get_settings()
    concurrency = _clamp_concurrency(settings.deep_scan_concurrency, default=3)

    jobs = await _load_jobs_for_bg(
        session,
        row,
        statuses=[
            JobStatus.NEW.value,
            JobStatus.JD_READY.value,
            JobStatus.SCAN_FAILED.value,
        ],
    )
    job_ids = [j.id for j in jobs]
    bg_id = row.id
    items: list[dict[str, Any]] = []
    progress: dict[str, Any] = {
        "done": 0,
        "total": len(job_ids),
        "items": items,
        "concurrency": concurrency,
    }
    ids = _progress_job_ids(row)
    if ids is not None:
        progress["job_ids"] = ids
    row.progress = json.dumps(progress)
    await session.commit()

    if not job_ids:
        row.status = BackgroundJobStatus.COMPLETED.value
        row.progress = json.dumps(progress)
        await session.commit()
        return

    sem = asyncio.Semaphore(concurrency)
    progress_lock = asyncio.Lock()
    factory = get_session_factory()

    async def one(job_id: int) -> None:
        async with sem:
            if await _bg_cancelled(bg_id):
                return
            try:
                async with factory() as work:
                    job = await work.get(Job, job_id)
                    if not job:
                        result = {"job_id": job_id, "ok": False, "reason": "job_missing"}
                    else:
                        result = await deep_scan_job(work, job)
                        await work.commit()
            except Exception as exc:  # noqa: BLE001
                result = {"job_id": job_id, "ok": False, "reason": str(exc)[:400]}
            await _record_progress(
                bg_id=bg_id,
                progress=progress,
                items=items,
                result=result,
                lock=progress_lock,
            )

    await asyncio.gather(*(one(jid) for jid in job_ids))

    async with factory() as session2:
        row2 = await session2.get(BackgroundJob, bg_id)
        if not row2:
            return
        if row2.cancel_requested:
            row2.status = BackgroundJobStatus.CANCELLED.value
        else:
            row2.status = BackgroundJobStatus.COMPLETED.value
        row2.progress = json.dumps(progress)
        row2.updated_at = _utcnow()
        await session2.commit()


async def _exec_tailor(session, row: BackgroundJob) -> None:
    from app.services.tailor import tailor_job as _tailor

    settings = get_settings()
    concurrency = _clamp_concurrency(settings.tailor_concurrency, default=5)

    # Capture generation persona BEFORE rebuilding progress
    gen_pid = _generate_persona_id(row) or row.persona_id
    force = _force_regenerate(row)

    # Listing ownership/status does not decide whether this persona needs a package.
    jobs = await _load_jobs_for_bg(
        session,
        row,
        statuses=[
            JobStatus.NEW.value,
            JobStatus.JD_READY.value,
            JobStatus.SCAN_FAILED.value,
            JobStatus.NEEDS_MANUAL.value,
            # Legacy package-derived listing states are accepted until startup healing.
            JobStatus.RESUME_READY.value,
            JobStatus.APPLYING.value,
            JobStatus.APPLIED.value,
        ],
        match_listing_persona=False,
    )
    # A forced run retailors everything picked, overwriting packages this persona already has.
    if not force:
        jobs = await _filter_jobs_needing_persona_package(session, jobs, persona_id=gen_pid)
    # Still need a usable JD
    jobs = [j for j in jobs if j.description and len(j.description) >= 50]
    job_ids = [j.id for j in jobs]
    bg_id = row.id
    items: list[dict[str, Any]] = []
    progress: dict[str, Any] = {
        "done": 0,
        "total": len(job_ids),
        "items": items,
        "generate_as": gen_pid,
        "concurrency": concurrency,
        "force": force,
    }
    ids = _progress_job_ids(row)
    if ids is not None:
        progress["job_ids"] = ids
    row.progress = json.dumps(progress)
    await session.commit()

    if not job_ids:
        row.status = BackgroundJobStatus.COMPLETED.value
        row.progress = json.dumps(progress)
        await session.commit()
        return

    sem = asyncio.Semaphore(concurrency)
    progress_lock = asyncio.Lock()
    factory = get_session_factory()

    async def one(job_id: int) -> None:
        async with sem:
            if await _bg_cancelled(bg_id):
                return
            try:
                async with factory() as work:
                    job = await work.get(Job, job_id)
                    if not job:
                        result: dict[str, Any] = {
                            "job_id": job_id,
                            "ok": False,
                            "reason": "job_missing",
                        }
                    else:
                        result = await _tailor(work, job, persona_id=gen_pid)
                        await work.commit()
                        if result.get("ok"):
                            log.info(
                                "bg tailor ok job_id=%s persona=%s",
                                job_id,
                                gen_pid,
                            )
                        else:
                            log.error(
                                "bg tailor fail job_id=%s persona=%s reason=%s",
                                job_id,
                                gen_pid,
                                result.get("reason"),
                            )
            except Exception as exc:  # noqa: BLE001
                reason = str(exc)[:400]
                log.exception(
                    "bg tailor crashed job_id=%s persona=%s reason=%s",
                    job_id,
                    gen_pid,
                    reason,
                )
                result = {"job_id": job_id, "ok": False, "reason": reason}
                try:
                    async with factory() as work:
                        job = await work.get(Job, job_id)
                        if job:
                            job.notes = f"tailor_failed:{reason}"
                            await work.commit()
                except Exception:  # noqa: BLE001
                    pass
            await _record_progress(
                bg_id=bg_id,
                progress=progress,
                items=items,
                result=result,
                lock=progress_lock,
            )

    await asyncio.gather(*(one(jid) for jid in job_ids))

    async with factory() as session2:
        row2 = await session2.get(BackgroundJob, bg_id)
        if not row2:
            return
        if row2.cancel_requested:
            row2.status = BackgroundJobStatus.CANCELLED.value
        else:
            row2.status = BackgroundJobStatus.COMPLETED.value
        row2.progress = json.dumps(progress)
        row2.updated_at = _utcnow()
        await session2.commit()


async def _exec_discover(session, row: BackgroundJob, *, mode: str = "standard") -> None:
    from app.services.discover import collect_new_listings, collect_site_listings
    from app.services.fetch_export import write_fetch_csv
    from app.services.pipeline import process_listing

    bg_id = row.id
    persona_id = row.persona_id
    factory = get_session_factory()
    try:
        prog0 = json.loads(row.progress or "{}")
    except json.JSONDecodeError:
        prog0 = {}
    opts = prog0.get("options") if isinstance(prog0.get("options"), dict) else {}
    site = str(opts.get("site") or prog0.get("site") or "").strip()
    window = str(opts.get("window") or prog0.get("window") or "7d").strip()
    max_jobs = int(opts.get("max_jobs") or prog0.get("max_jobs") or 100)
    domains = opts.get("domains") if isinstance(opts.get("domains"), list) else []
    roles = opts.get("roles") if isinstance(opts.get("roles"), list) else []

    progress: dict[str, Any] = {
        "done": 0,
        "total": 1,
        "items": [],
        "inserted": 0,
        "jd_ready": 0,
        "needs_scan": 0,
        "skipped": 0,
        "gated": 0,
        "rejected": 0,
        "phase": "collecting",
        "mode": mode,
        "site": site or None,
        "window": window if site else None,
        "max_jobs": max_jobs if site else None,
        "domains": domains or None,
        "roles": roles or None,
        "options": opts or None,
    }
    row.progress = json.dumps(progress)
    await session.commit()

    async def on_collect_progress(update: dict[str, Any]) -> None:
        progress.update(update)
        await _safe_write_progress(bg_id, progress)

    if site or mode == "site":
        if not site:
            row.status = BackgroundJobStatus.FAILED.value
            row.error = "site required for discover_site"
            await session.commit()
            return
        listings, meta = await collect_site_listings(
            site=site,
            window=window,
            max_jobs=max_jobs,
            domains=domains or None,
            roles=roles or None,
            on_progress=on_collect_progress,
        )
    else:
        listings, meta = await collect_new_listings(
            mode=mode,
            domains=domains or None,
            roles=roles or None,
            on_progress=on_collect_progress,
        )
    insert_limit = int(meta.get("insert_limit") or 50)
    # The whole catch, for the CSV. Popped before `meta` reaches `progress`,
    # which is stored as JSON on the row — a thousand job descriptions there
    # would make every poll of this job carry them back to the browser.
    found_rows = meta.pop("found_rows", None) or listings
    items: list[dict[str, Any]] = []
    progress.update(
        {
            "phase": "insert",
            "done": 0,
            "total": len(listings),
            "items": items,
            **meta,
        }
    )
    row.progress = json.dumps(progress)
    await session.commit()

    if not listings:
        row.status = BackgroundJobStatus.COMPLETED.value
        row.progress = json.dumps(progress)
        await session.commit()
        return

    gate_reasons = {
        "hybrid_or_onsite",
        "not_remote",
        "not_us",
        "non_us_only",
        "not_it_domain",
        "junior_title",
    }
    skip_reasons = {
        "company_already_applied",
        "company_blocked",
        "duplicate_url",
        "duplicate_external_id",
        "already_seen",
    }
    new_job_ids: list[int] = []
    for listing in listings:
        if await _bg_cancelled(bg_id):
            break
        if int(progress.get("inserted") or 0) >= insert_limit:
            progress["more_available"] = True
            break
        result_row = {
            "company": listing.get("company"),
            "title": listing.get("title"),
            "source": listing.get("source"),
            # What the window was judged on, and where to open the posting.
            # A list of jobs you cannot date or click is not a result.
            "posted_at": listing.get("posted_at"),
            "url": listing.get("url"),
            "ok": False,
            "reason": "",
        }
        try:
            async with factory() as work:
                result = await process_listing(
                    work,
                    listing,
                    persona_id=persona_id,
                    skip_content_gates=False,
                    insert_rejects=False,
                )
                await work.commit()
                if result.job and result.ok:
                    result_row["ok"] = True
                    result_row["job_id"] = result.job.id
                    result_row["seq_no"] = result.job.seq_no
                    result_row["status"] = result.job.status
                    progress["inserted"] = int(progress.get("inserted") or 0) + 1
                    if result.job.status == JobStatus.JD_READY.value:
                        progress["jd_ready"] = int(progress.get("jd_ready") or 0) + 1
                    elif result.job.status == JobStatus.NEW.value:
                        progress["needs_scan"] = int(progress.get("needs_scan") or 0) + 1
                        new_job_ids.append(result.job.id)
                elif result.skipped or not result.ok:
                    result_row["reason"] = result.reason or "skipped"
                    if result.reason in skip_reasons:
                        progress["skipped"] = int(progress.get("skipped") or 0) + 1
                    elif result.reason in gate_reasons:
                        progress["gated"] = int(progress.get("gated") or 0) + 1
                        progress["rejected"] = int(progress.get("gated") or 0)
                    else:
                        progress["gated"] = int(progress.get("gated") or 0) + 1
                        progress["rejected"] = int(progress.get("gated") or 0)
                else:
                    result_row["reason"] = result.reason or "unknown"
                    progress["skipped"] = int(progress.get("skipped") or 0) + 1
        except Exception as exc:  # noqa: BLE001
            result_row["reason"] = str(exc)[:400]
            progress["skipped"] = int(progress.get("skipped") or 0) + 1
        items.append(result_row)
        progress["done"] = len(items)
        progress["items"] = items[-DISCOVER_ITEMS_SHOWN:]
        async with factory() as session2:
            row2 = await session2.get(BackgroundJob, bg_id)
            if not row2:
                return
            row2.progress = json.dumps(progress)
            row2.updated_at = _utcnow()
            await session2.commit()

    # Every job the run found, on disk, with what became of each. The table
    # shows only what cleared the gates; a fetch worth waiting for should
    # also leave behind the rows it decided against.
    try:
        csv_path = write_fetch_csv(
            site=site or mode,
            window=window if site else "",
            listings=found_rows,
            outcomes=items,
        )
        if csv_path:
            progress["csv_file"] = str(csv_path)
            progress["csv_name"] = csv_path.name
            progress["csv_rows"] = len(found_rows)
    except Exception as exc:  # noqa: BLE001
        # A fetch that reached the table is not a failed fetch.
        log.warning("fetch csv export failed: %s", exc)

    if new_job_ids and not await _bg_cancelled(bg_id):
        scan_row = await enqueue(
            kind=BackgroundJobKind.DEEP_SCAN,
            persona_id=persona_id,
            job_ids=new_job_ids,
        )
        progress["auto_scan_job_id"] = scan_row.id
        progress["auto_scan_queued"] = len(new_job_ids)

    async with factory() as session3:
        row3 = await session3.get(BackgroundJob, bg_id)
        if not row3:
            return
        if row3.cancel_requested:
            row3.status = BackgroundJobStatus.CANCELLED.value
        else:
            row3.status = BackgroundJobStatus.COMPLETED.value
        row3.progress = json.dumps(progress)
        row3.updated_at = _utcnow()
        await session3.commit()


async def _exec_discover_companies(session, row: BackgroundJob) -> None:
    from app.services.company_discovery import discover_companies

    bg_id = row.id
    progress: dict[str, Any] = {
        "done": 0,
        "total": 1,
        "items": [],
        "added": 0,
        "seen": 0,
    }
    row.progress = json.dumps(progress)
    await session.commit()
    # Release the worker session before long HTTP work so progress writers
    # and catalog inserts are not fighting an open SQLite writer.
    await session.close()

    factory = get_session_factory()

    async def on_progress(update: dict[str, Any]) -> None:
        progress.update(update)
        await _safe_write_progress(bg_id, progress)

    async def cancel_check() -> bool:
        return await _bg_cancelled(bg_id)

    async with factory() as work:
        result = await discover_companies(
            work,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        await work.commit()

    progress.update(
        {
            "done": max(int(progress.get("done") or 0), 1),
            "total": max(int(progress.get("total") or 1), 1),
            "ok": result.get("ok"),
            "provider": result.get("provider"),
            "mode": result.get("mode") or result.get("provider"),
            "added": result.get("added") or 0,
            "seen": result.get("seen") or 0,
            "probed": result.get("probed"),
            "candidates": result.get("candidates"),
            "queries_run": result.get("queries_run"),
            "pages_ok": result.get("pages_ok"),
            "fallback_from": result.get("fallback_from"),
            "errors": result.get("errors") or result.get("search_errors") or [],
            "message": result.get("message"),
            "ats": result.get("ats"),
            "boards": result.get("boards") or [],
            "items": (result.get("boards") or [])[:40],
        }
    )
    async with factory() as session2:
        row2 = await session2.get(BackgroundJob, bg_id)
        if not row2:
            return
        if row2.cancel_requested:
            row2.status = BackgroundJobStatus.CANCELLED.value
        elif not result.get("ok"):
            row2.status = BackgroundJobStatus.FAILED.value
            row2.error = result.get("message") or result.get("error") or "company_discovery_failed"
        else:
            row2.status = BackgroundJobStatus.COMPLETED.value
        row2.progress = json.dumps(progress)
        row2.updated_at = _utcnow()
        await session2.commit()
