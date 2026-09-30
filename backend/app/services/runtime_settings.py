"""Settings the operator changes from the UI, persisted across restarts.

Values in .env are deployment config and need an edit plus a restart. The ATS
floor is a per-session working decision — raise it for a careful batch, drop it
when a niche posting cannot reach it — so it lives here instead, alongside the
active persona in personas.yml rather than baked into the environment.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import yaml

from app.config import get_settings

log = logging.getLogger("poong.runtime")

_FILENAME = "runtime.yml"

DEFAULTS: dict[str, Any] = {
    # Minimum ATS keyword score a generated resume must reach; 0 disables the
    # check. Each retry below the floor is a full tailor call, hence the cap.
    "min_ats_score": 0,
    "ats_max_attempts": 3,
    # When the Today counter last started counting, as an ISO timestamp.
    # None means it has never been reset, and the count falls back to the
    # start of the local day.
    "applied_counter_since": None,
    # The accent a generated resume is printed in, as #rrggbb. None means each
    # persona keeps the accent its own stylesheet is written in.
    "resume_accent": None,
    # The most one ChatGPT reply may take. A ceiling, not a delay: the job
    # moves on the moment the reply finishes.
    "chatgpt_timeout_seconds": 360,
}


def _path():
    return get_settings().config_dir / _FILENAME


def load() -> dict[str, Any]:
    path = _path()
    values = dict(DEFAULTS)
    if not path.exists():
        return values
    try:
        stored = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        log.warning("runtime.yml is unreadable — falling back to defaults")
        return values
    if isinstance(stored, dict):
        values.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return values


def get(key: str) -> Any:
    return load().get(key, DEFAULTS.get(key))


def min_ats_score() -> int:
    """The floor, falling back to .env when the operator has not set one."""
    stored = load().get("min_ats_score")
    if stored in (None, ""):
        stored = get_settings().min_ats_score
    return max(0, min(100, int(stored or 0)))


def max_attempts() -> int:
    stored = load().get("ats_max_attempts") or get_settings().ats_max_attempts
    return max(1, min(6, int(stored or 1)))


def resume_accent() -> str | None:
    """The accent chosen in the UI, or None to keep each stylesheet's own."""
    stored = load().get("resume_accent")
    from app.services.resume_accent import is_valid

    return str(stored) if is_valid(stored) else None


def chatgpt_timeout_seconds() -> int:
    try:
        stored = int(load().get("chatgpt_timeout_seconds") or 0)
    except (TypeError, ValueError):
        stored = 0
    return max(60, min(1800, stored or DEFAULTS["chatgpt_timeout_seconds"]))


def update(**changes: Any) -> dict[str, Any]:
    """Persist changed values, ignoring keys we do not own."""
    values = load()
    for key, value in changes.items():
        if key not in DEFAULTS or value is None:
            continue
        if key == "min_ats_score":
            value = max(0, min(100, int(value)))
        elif key == "ats_max_attempts":
            value = max(1, min(6, int(value)))
        elif key == "applied_counter_since":
            value = str(value)
        elif key == "resume_accent":
            from app.services.resume_accent import normalise

            # Raises on anything that is not #rrggbb. The value is substituted
            # into a stylesheet, so it must never be free text.
            value = normalise(value)
        elif key == "chatgpt_timeout_seconds":
            value = max(60, min(1800, int(value)))
        values[key] = value
    _path().write_text(yaml.safe_dump(values, sort_keys=True), encoding="utf-8")
    return values


def applied_counter_since() -> datetime | None:
    """When the Today counter started counting, or None for local midnight.

    Stored as an aware ISO timestamp. An unreadable value is treated as unset
    rather than raised: a corrupt preference should show a stale count, not
    break the header.
    """
    raw = load().get("applied_counter_since")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        log.warning("applied_counter_since is not a timestamp: %r", raw)
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def reset_applied_counter(moment: datetime | None = None) -> datetime:
    """Start the Today counter from now. Returns the new marker."""
    marker = (moment or datetime.now()).astimezone()
    update(applied_counter_since=marker.isoformat())
    return marker
