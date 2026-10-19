from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.job_tables import job_model, resolve_job
from app.services.folder_writer import job_folder_name, safe_company_slug
from app.services.persona import all_persona_ids


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

    renumber moves every folder to a __renum_ temp name before moving any to its
    final name, so an interruption leaves the remainder stranded under temp
    names. Runs at startup. A job now lives in exactly one persona's table and
    its folder sits under ``output_root/{persona}/``.
    """
    root = get_settings().output_root
    if not root.exists():
        return {"healed": 0, "blocked": 0, "orphaned": 0}

    healed = blocked = orphaned = 0

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
            resolved = await resolve_job(session, job_id)
            job = resolved[1] if resolved else None
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
            if job.persona_id == persona_dir.name:
                job.folder_path = str(moved)
            healed += 1

    if healed:
        await session.commit()

    return {"healed": healed, "blocked": blocked, "orphaned": orphaned}


def _collect_folders_for_job(
    *,
    persona_id: str,
    old_seq: int | None,
    company: str,
    folder_path: str | None,
) -> list[Path]:
    """On-disk folders for this listing, under its own persona's dir only."""
    found: dict[str, Path] = {}
    persona_dir = get_settings().output_root / persona_id
    if folder_path:
        p = Path(folder_path)
        if p.exists() and p.is_dir() and _under_output_root(p):
            found[str(p.resolve())] = p
    if old_seq is not None and persona_dir.exists():
        candidate = persona_dir / job_folder_name(old_seq, company)
        if candidate.exists() and candidate.is_dir():
            found[str(candidate.resolve())] = candidate
    return list(found.values())


def _temp_folder_name(job_id: int, company: str) -> str:
    return f"__renum_{job_id}__{safe_company_slug(company)}"


async def renumber_all_jobs(session: AsyncSession) -> dict[str, Any]:
    """Renumber every persona's own table 1..N and rename its folders to match."""
    agg: dict[str, Any] = {
        "ok": True,
        "changed": False,
        "renumbered": 0,
        "folders_renamed": 0,
        "folders_stuck": 0,
        "total": 0,
        "seq_max": 0,
    }
    by_persona: dict[str, Any] = {}
    for pid in all_persona_ids():
        r = await renumber_persona(session, pid)
        by_persona[pid] = r
        agg["changed"] = agg["changed"] or bool(r.get("changed"))
        agg["renumbered"] += r.get("renumbered", 0)
        agg["folders_renamed"] += r.get("folders_renamed", 0)
        agg["folders_stuck"] += r.get("folders_stuck", 0)
        agg["total"] += r.get("total", 0)
        agg["seq_max"] = max(agg["seq_max"], r.get("seq_max", 0))
    agg["by_persona"] = by_persona
    return agg


async def renumber_persona(session: AsyncSession, persona_id: str) -> dict[str, Any]:
    """Reassign seq_no to 1..N within one persona's table and rename its folders."""
    model = job_model(persona_id)
    jobs = (
        await session.execute(
            select(model).order_by(model.seq_no.is_(None), model.seq_no.asc(), model.id.asc())
        )
    ).scalars().all()
    total = len(jobs)
    plan: list[tuple[Any, int | None, int]] = [
        (job, job.seq_no, idx) for idx, job in enumerate(jobs, start=1)
    ]
    already = all(old == new for _, old, new in plan)
    if already:
        repaired = 0
        for job, _, new_seq in plan:
            repaired += _sync_path_after_rename(job, persona_id, new_seq)
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

    # Phase A: move folders to temp names (avoids collisions like 5→3 while 3 exists)
    temp_moves: list[tuple[Any, list[Path], int]] = []
    for job, old_seq, new_seq in plan:
        folders = _collect_folders_for_job(
            persona_id=persona_id,
            old_seq=old_seq,
            company=job.company,
            folder_path=job.folder_path,
        )
        temps: list[Path] = []
        for folder in folders:
            temp = folder.parent / _temp_folder_name(job.id, job.company)
            moved = _safe_rename(folder, temp)
            if moved is not None:
                temps.append(moved)
            elif folder.exists():
                temps.append(folder)
        temp_moves.append((job, temps, new_seq))

    # Clear seq_no to satisfy UNIQUE, then assign 1..N.
    for job, _, _ in plan:
        job.seq_no = None
    await session.flush()

    folders_renamed = folders_stuck = changed_jobs = 0
    for job, temps, new_seq in temp_moves:
        old_seq = next(old for j, old, _ in plan if j.id == job.id)
        if old_seq != new_seq:
            changed_jobs += 1
        job.seq_no = new_seq
        final_name = job_folder_name(new_seq, job.company)
        new_path: str | None = None
        for temp in temps:
            if not temp.exists():
                continue
            dst = temp.parent / final_name
            if temp.name == final_name:
                final = temp
            else:
                final = _safe_rename(temp, dst)
                if final is None:
                    folders_stuck += 1
                    new_path = str(temp)
                    continue
            _update_meta_seq(final, new_seq, job.id)
            new_path = str(final)
            folders_renamed += 1
        if new_path:
            job.folder_path = new_path
        elif job.folder_path:
            job.folder_path = _rewrite_seq_in_path(job.folder_path, new_seq, job.company)

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
    if "\\" in path and "/" not in path:
        return str(Path(path).parent / name)
    return f"{pre}{name}" if pre else name


def _sync_path_after_rename(job: Any, persona_id: str, new_seq: int) -> int:
    """When seq already correct, still point folder_path at the canonical dir."""
    root = get_settings().output_root
    if not root.exists():
        return 0
    name = job_folder_name(new_seq, job.company)
    canonical = root / persona_id / name
    if canonical.exists() and job.folder_path != str(canonical):
        job.folder_path = str(canonical)
        _update_meta_seq(canonical, new_seq, job.id)
        return 1
    return 0
