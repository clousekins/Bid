"""Screenshot what was actually submitted.

Applications go out through a real browser, and until now nothing recorded what
the form contained at the moment it was sent. When an employer never replies
there is no way to tell a bad resume from a form that silently failed, or to
check which values the filler actually put in the fields.

Captures are best-effort by design: a screenshot failing must never take down
an apply session that is otherwise working.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("poong.capture")

# Stages worth keeping. Pre-submit is the evidence of what we sent; post-submit
# is the evidence of what the employer's page said back.
PRE_SUBMIT = "pre-submit"
POST_SUBMIT = "post-submit"

SCREENSHOT_PREFIX = "submission"
_SAFE_STAGE = re.compile(r"[^a-z0-9-]+")


def screenshot_name(stage: str, *, when: datetime | None = None) -> str:
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    safe = _SAFE_STAGE.sub("-", stage.lower()).strip("-") or "capture"
    return f"{SCREENSHOT_PREFIX}-{stamp}-{safe}.png"


def list_captures(folder: str | Path | None) -> list[Path]:
    """Captures in a package folder, newest first."""
    if not folder:
        return []
    root = Path(folder)
    if not root.is_dir():
        return []
    shots = [
        p
        for p in root.iterdir()
        if p.is_file()
        and p.suffix.lower() == ".png"
        and p.name.startswith(f"{SCREENSHOT_PREFIX}-")
    ]
    shots.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return shots


async def capture_submission(
    page: Any,
    folder: str | Path | None,
    stage: str,
    *,
    emit: Any = None,
) -> Path | None:
    """Save a full-page screenshot into the package folder.

    Returns the path, or None when there is nowhere to write or the capture
    fails. Never raises — the apply session must survive a failed screenshot.
    """
    if page is None or not folder:
        return None
    root = Path(folder)
    try:
        if not root.is_dir():
            return None
        target = root / screenshot_name(stage)
        await page.screenshot(path=str(target), full_page=True)
    except Exception as exc:  # noqa: BLE001
        # A closed page, a navigation mid-shot, a locked file — none of these
        # are worth failing an application over.
        log.warning("submission capture failed (%s): %s", stage, exc)
        if emit:
            emit(f"Screenshot failed ({stage}): {exc}", "warn")
        return None
    if emit:
        emit(f"Saved {stage} screenshot: {target.name}")
    return target
