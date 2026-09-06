from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import Job
from app.services.folder_writer import job_folder_name, safe_company_slug


def _under_output_root(path: Path) -> bool:
    root = get_settings().output_root.resolve()
    try:
        path.resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _update_meta_seq(folder: Path, seq_no: int, job_id: int) -> None:
    meta_path = folder / "meta.json"
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    if not isinstance(meta, dict):
        return
    meta["seq_no"] = seq_no
    meta["job_id"] = job_id
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _safe_rename(src: Path, dst: Path) -> Path | None:
    if not src.exists() or not src.is_dir():
        return None
    if not _under_output_root(src):
        return None
    if dst.exists():
        if src.resolve() == dst.resolve():
            return dst
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return dst


_TEMP_FOLDER_RE = re.compile(r"^__renum_(?P<job_id>\d+)__")


async def heal_stuck_renumber_folders(session: AsyncSession) -> dict[str, Any]:
    """Finish a renumber that died between its two rename phases.

    renumber_all_jobs moves every folder to a __renum_ temp name before moving
    any to its final name, so an interruption anywhere in phase B leaves the
    remainder stranded under temp names with nothing to repair them. Runs at
    startup, the same way heal_stuck_jobs recovers interrupted background jobs.
    """
    root = get_settings().output_root
    if not root.exists():
        return {"healed": 0, "blocked": 0, "orphaned": 0}

    healed = blocked = orphaned = 0
    repaired_paths: dict[int, dict[str, str]] = {}

    for persona_dir in sorted(root.iterdir()):
        if not persona_dir.is_dir():
            continue
        for folder in sorted(persona_dir.iterdir()):
            if not folder.is_dir():
                continue
            match = _TEMP_FOLDER_RE.match(folder.name)
            if not match:
                continue
            job_id = int(match.group("job_id"))
            job = await session.get(Job, job_id)
            if job is None or job.seq_no is None:
                orphaned += 1
                continue
            dst = persona_dir / job_folder_name(job.seq_no, job.company)
            if dst.exists() and dst.resolve() != folder.resolve():
                blocked += 1
                continue
            moved = _safe_rename(folder, dst)
            if moved is None:
                blocked += 1
                continue
            _update_meta_seq(moved, job.seq_no, job.id)
            repaired_paths.setdefault(job_id, {})[persona_dir.name] = str(moved)
            healed += 1

    # Point the DB at the recovered folders.
    for job_id, by_persona in repaired_paths.items():
        job = (
            await session.execute(
                select(Job).options(selectinload(Job.packages)).where(Job.id == job_id)
            )
        ).scalar_one_or_none()
        if job is None:
            continue
        for pkg in job.packages or []:
            if pkg.persona_id in by_persona:
                pkg.folder_path = by_persona[pkg.persona_id]
        if job.persona_id in by_persona:
            job.folder_path = by_persona[job.persona_id]
        elif by_persona:
            job.folder_path = next(iter(by_persona.values()))
    if repaired_paths:
        await session.commit()

    return {"healed": healed, "blocked": blocked, "orphaned": orphaned}


def _collect_folders_for_job(
    *,
    old_seq: int | None,
    company: str,
    folder_paths: list[str | None],
) -> list[Path]:
    """Find on-disk folders belonging to this listing (any persona)."""
    found: dict[str, Path] = {}
    root = get_settings().output_root
    for fp in folder_paths:
        if not fp:
            continue
        p = Path(fp)
        if p.exists() and p.is_dir() and _under_output_root(p):
            found[str(p.resolve())] = p
    if old_seq is not None and root.exists():
        name = job_folder_name(old_seq, company)
        for child in root.iterdir():
            if not child.is_dir():
                continue
            candidate = child / name
            if candidate.exists() and candidate.is_dir():
                found[str(candidate.resolve())] = candidate
    return list(found.values())


def _temp_folder_name(job_id: int, company: str) -> str:
    return f"__renum_{job_id}__{safe_company_slug(company)}"


async def renumber_all_jobs(session: AsyncSession) -> dict[str, Any]:
    """Reassign seq_no to 1..N and rename resume folders under every persona root."""
    jobs = (
        await session.execute(
            select(Job)
            .options(selectinload(Job.packages))
            .order_by(Job.seq_no.is_(None), Job.seq_no.asc(), Job.id.asc())
        )
    ).scalars().all()
    total = len(jobs)
    plan: list[tuple[Job, int | None, int]] = [
        (job, job.seq_no, idx) for idx, job in enumerate(jobs, start=1)
    ]
    already = all(old == new for _, old, new in plan)
    if already:
        # Still repair folder_path / meta if folders drifted
        repaired = 0
        for job, _, new_seq in plan:
            repaired += _sync_paths_after_rename(job, new_seq)
        if repaired:
            await session.flush()
        return {
            "ok": True,
            "changed": repaired > 0,
            "renumbered": 0,
            "folders_renamed": repaired,
            "total": total,
            "seq_max": total if total else 0,
        }

    # Phase A: move folders to temp names (avoids collisions like 5→3 while 3 still exists)
    temp_moves: list[tuple[Job, list[Path], int]] = []
    for job, old_seq, new_seq in plan:
        pkgs = list(job.packages or [])
        folders = _collect_folders_for_job(
            old_seq=old_seq,
            company=job.company,
            folder_paths=[job.folder_path, *[p.folder_path for p in pkgs]],
        )
        temps: list[Path] = []
        for folder in folders:
            persona_dir = folder.parent
            temp = persona_dir / _temp_folder_name(job.id, job.company)
            moved = _safe_rename(folder, temp)
            if moved is not None:
                temps.append(moved)
            elif folder.exists():
                temps.append(folder)
        temp_moves.append((job, temps, new_seq))

    # Clear seq_no to satisfy UNIQUE, then assign 1..N
    for job, _, _ in plan:
        job.seq_no = None
    await session.flush()

    folders_renamed = 0
    folders_stuck = 0
    changed_jobs = 0
    for job, temps, new_seq in temp_moves:
        old_seq = next(old for j, old, new in plan if j.id == job.id)
        if old_seq != new_seq:
            changed_jobs += 1
        job.seq_no = new_seq
        final_name = job_folder_name(new_seq, job.company)
        new_paths: list[str] = []
        for temp in temps:
            if not temp.exists():
                continue
            dst = temp.parent / final_name
            if temp.name == final_name:
                final = temp
            else:
                final = _safe_rename(temp, dst)
                if final is None:
                    # Left under its temp name. Count it as stuck rather than
                    # renamed — reporting this as success is what let 1,444
                    # folders sit corrupted while the API said everything was
                    # fine. heal_stuck_renumber_folders picks it up next boot.
                    folders_stuck += 1
                    final = temp
                    new_paths.append(str(final))
                    continue
            _update_meta_seq(final, new_seq, job.id)
            new_paths.append(str(final))
            folders_renamed += 1

        # Update package + job folder_path strings
        by_persona: dict[str, str] = {}
        for path_str in new_paths:
            p = Path(path_str)
            by_persona[p.parent.name] = path_str

        for pkg in job.packages or []:
            if pkg.persona_id in by_persona:
                pkg.folder_path = by_persona[pkg.persona_id]
            elif pkg.folder_path:
                # Rewrite seq prefix in stored path if folder was moved via sweep
                pkg.folder_path = _rewrite_seq_in_path(
                    pkg.folder_path, new_seq, job.company
                )

        if job.persona_id in by_persona:
            job.folder_path = by_persona[job.persona_id]
        elif new_paths:
            job.folder_path = new_paths[0]
        elif job.folder_path:
            job.folder_path = _rewrite_seq_in_path(
                job.folder_path, new_seq, job.company
            )

    await session.flush()
    return {
        "ok": True,
        "changed": True,
        "renumbered": changed_jobs,
        "folders_renamed": folders_renamed,
        "folders_stuck": folders_stuck,
        "total": total,
        "seq_max": total if total else 0,
    }


_SEQ_PREFIX = re.compile(r"^(?P<pre>.*[/\\])?(?P<seq>\d+)-(?P<rest>[^/\\]+)$")


def _rewrite_seq_in_path(path: str, new_seq: int, company: str) -> str:
    name = job_folder_name(new_seq, company)
    m = _SEQ_PREFIX.match(path.replace("\\", "/"))
    if not m:
        return path
    pre = m.group("pre") or ""
    # preserve original separators loosely
    if "\\" in path and "/" not in path:
        return str(Path(path).parent / name)
    return f"{pre}{name}" if pre else name


def _sync_paths_after_rename(job: Job, new_seq: int) -> int:
    """When seq already correct, still point folder_path at canonical location if present."""
    root = get_settings().output_root
    name = job_folder_name(new_seq, job.company)
    repaired = 0
    if not root.exists():
        return 0
    for pkg in job.packages or []:
        canonical = root / pkg.persona_id / name
        if canonical.exists() and pkg.folder_path != str(canonical):
            pkg.folder_path = str(canonical)
            _update_meta_seq(canonical, new_seq, job.id)
            repaired += 1
    if job.persona_id:
        canonical = root / job.persona_id / name
        if canonical.exists() and job.folder_path != str(canonical):
            job.folder_path = str(canonical)
            repaired += 1
    return repaired
