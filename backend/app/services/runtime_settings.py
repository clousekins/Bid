"""Settings the operator changes from the UI, persisted across restarts.

Values in .env are deployment config and need an edit plus a restart. The ATS
floor is a per-session working decision — raise it for a careful batch, drop it
when a niche posting cannot reach it — so it lives here instead, alongside the
active persona in personas.yml rather than baked into the environment.
"""
from __future__ import annotations

import logging
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
        values[key] = value
    _path().write_text(yaml.safe_dump(values, sort_keys=True), encoding="utf-8")
    return values
