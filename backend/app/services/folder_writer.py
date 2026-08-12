from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import get_settings


_SAFE = re.compile(r"[^\w\s\-]+")
_SPACE = re.compile(r"\s+")


def safe_company_slug(company: str) -> str:
    cleaned = _SAFE.sub("", company or "Company")
    cleaned = _SPACE.sub("-", cleaned.strip())
    return cleaned[:80] or "Company"


def job_folder_name(seq_no: int, company: str) -> str:
    return f"{seq_no}-{safe_company_slug(company)}"


def ensure_job_folder(persona_id: str, seq_no: int, company: str) -> Path:
    root = get_settings().output_root / persona_id / job_folder_name(seq_no, company)
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_meta(folder: Path, meta: dict[str, Any]) -> None:
    (folder / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def write_jd(folder: Path, description: str) -> None:
    (folder / "JD.txt").write_text(description or "", encoding="utf-8")


def remove_job_folder(folder_path: str | Path | None) -> bool:
    """Delete a job output folder if it lives under the configured output root."""
    if not folder_path:
        return False
    path = Path(folder_path)
    if not path.exists() or not path.is_dir():
        return False
    root = get_settings().output_root.resolve()
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    import shutil

    shutil.rmtree(resolved, ignore_errors=True)
    return not resolved.exists()


def remove_job_output(
    *,
    folder_path: str | None,
    persona_id: str,
    seq_no: int | None,
    company: str,
) -> bool:
    """Remove folder_path and the canonical seq-company folder under output_root."""
    removed = False
    if folder_path:
        removed = remove_job_folder(folder_path) or removed
    if seq_no is not None:
        computed = get_settings().output_root / persona_id / job_folder_name(seq_no, company)
        removed = remove_job_folder(computed) or removed
    return removed


def remove_all_persona_outputs(
    *,
    seq_no: int | None,
    company: str,
    folder_paths: list[str | None] | None = None,
    persona_ids: list[str] | None = None,
) -> bool:
    """Remove package folders across all persona roots for a seq/company listing."""
    removed = False
    for fp in folder_paths or []:
        removed = remove_job_folder(fp) or removed
    if seq_no is None:
        return removed
    root = get_settings().output_root
    name = job_folder_name(seq_no, company)
    seen: set[str] = set()
    for pid in persona_ids or []:
        if not pid or pid in seen:
            continue
        seen.add(pid)
        removed = remove_job_folder(root / pid / name) or removed
    # Also sweep any persona directory that has this seq-company folder
    if root.exists():
        for child in root.iterdir():
            if not child.is_dir() or child.name in seen:
                continue
            candidate = child / name
            if candidate.is_dir():
                removed = remove_job_folder(candidate) or removed
    return removed


def sync_job_folder(
    *,
    persona_id: str,
    seq_no: int,
    company: str,
    title: str,
    url: str,
    location: str | None,
    status: str,
    description: str | None,
    job_id: int,
    extra: dict[str, Any] | None = None,
) -> Path:
    folder = ensure_job_folder(persona_id, seq_no, company)
    meta: dict[str, Any] = {
        "job_id": job_id,
        "seq_no": seq_no,
        "company": company,
        "title": title,
        "url": url,
        "location": location,
        "status": status,
        "persona_id": persona_id,
    }
    if extra:
        meta.update(extra)
    write_meta(folder, meta)
    if description:
        write_jd(folder, description)
    return folder
