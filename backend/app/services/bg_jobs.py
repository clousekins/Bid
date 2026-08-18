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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_wake() -> asyncio.Event:
    global _wake
    if _wake is None:
        _wake = asyncio.Event()
    return _wake


def _get_locks() -> dict[str, asyncio.Lock]:
    global _locks
    if _locks is None:
        _locks = {
            BackgroundJobKind.DEEP_SCAN.value: asyncio.Lock(),
            BackgroundJobKind.TAILOR.value: asyncio.Lock(),
            BackgroundJobKind.DISCOVER.value: asyncio.Lock(),
        }
    return _locks


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
) -> BackgroundJob:
    factory = get_session_factory()
    ids = sorted({int(i) for i in (job_ids or []) if i is not None})
    progress: dict[str, Any] = {"done": 0, "total": 0, "items": []}
    if ids:
        progress["job_ids"] = ids
    if generate_as:
        progress["generate_as"] = generate_as
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
    while True:
        try:
            picked = await _pick_next()
            if not picked:
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=2.0)
                except TimeoutError:
                    pass
                continue
            await _run_job(picked)
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
                return row.id
    return None


async def _run_job(job_id: int) -> None:
    locks = _get_locks()
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(BackgroundJob, job_id)
        if not row or row.status != BackgroundJobStatus.QUEUED.value:
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
            row.status = BackgroundJobStatus.RUNNING.value
            row.updated_at = _utcnow()
            await session.commit()

            try:
                if row.kind == BackgroundJobKind.DEEP_SCAN.value:
                    await _exec_deep_scan(session, row)
                elif row.kind == BackgroundJobKind.TAILOR.value:
                    await _exec_tailor(session, row)
                elif row.kind == BackgroundJobKind.DISCOVER.value:
                    await _exec_discover(session, row)
                else:
                    row.status = BackgroundJobStatus.FAILED.value
                    row.error = f"unknown kind {row.kind}"
                    await session.commit()
            except Exception as exc:  # noqa: BLE001
                row.status = BackgroundJobStatus.FAILED.value
                row.error = str(exc)
                await session.commit()


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


async def _exec_discover(session, row: BackgroundJob) -> None:
    from app.services.discover import collect_new_listings
    from app.services.pipeline import process_listing

    bg_id = row.id
    persona_id = row.persona_id
    listings, meta = await collect_new_listings()
    insert_limit = int(meta.get("insert_limit") or 50)
    items: list[dict[str, Any]] = []
    progress: dict[str, Any] = {
        "done": 0,
        "total": len(listings),
        "items": items,
        "inserted": 0,
        "skipped": 0,
        "rejected": 0,
        **meta,
    }
    row.progress = json.dumps(progress)
    await session.commit()

    if not listings:
        row.status = BackgroundJobStatus.COMPLETED.value
        row.progress = json.dumps(progress)
        await session.commit()
        return

    factory = get_session_factory()
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
                    insert_rejects=True,
                )
                await work.commit()
                if result.job and result.ok:
                    result_row["ok"] = True
                    result_row["job_id"] = result.job.id
                    result_row["seq_no"] = result.job.seq_no
                    progress["inserted"] = int(progress.get("inserted") or 0) + 1
                elif result.skipped or not result.ok:
                    result_row["reason"] = result.reason or "skipped"
                    if result.reason in {
                        "company_already_applied",
                        "company_blocked",
                        "duplicate_url",
                        "duplicate_external_id",
                        "already_seen",
                    }:
                        progress["skipped"] = int(progress.get("skipped") or 0) + 1
                    else:
                        progress["rejected"] = int(progress.get("rejected") or 0) + 1
                        if result.job:
                            result_row["job_id"] = result.job.id
                else:
                    result_row["reason"] = result.reason or "unknown"
                    progress["skipped"] = int(progress.get("skipped") or 0) + 1
        except Exception as exc:  # noqa: BLE001
            result_row["reason"] = str(exc)[:400]
            progress["skipped"] = int(progress.get("skipped") or 0) + 1
        items.append(result_row)
        progress["done"] = len(items)
        progress["items"] = items[-50:]
        async with factory() as session2:
            row2 = await session2.get(BackgroundJob, bg_id)
            if not row2:
                return
            row2.progress = json.dumps(progress)
            row2.updated_at = _utcnow()
            await session2.commit()

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
