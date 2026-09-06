"""Companies a persona should not be shown jobs at.

A resume claiming to work at UnitedHealth Group cannot be sent to UnitedHealth
Group — the recruiter reading it knows who works there. The same holds for the
subsidiary: an Optum posting is the same employer.

These jobs are hidden per profile, never deleted. Brian cannot apply to his own
employer, but Bing Lin can, and the listing has to stay in the table for her.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import yaml

from app.config import get_settings
from app.validators.dedupe import normalize_company

log = logging.getLogger("poong.employers")


@lru_cache(maxsize=1)
def _load_groups(stamp: float) -> dict[str, tuple[str, ...]]:
    """Parent -> brands, from config/company_groups.yml."""
    path = get_settings().config_dir / "company_groups.yml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        log.warning("company_groups.yml is unreadable — subsidiaries not expanded")
        return {}
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, dict):
        return {}
    return {
        str(parent): tuple(str(b) for b in (brands or []))
        for parent, brands in groups.items()
    }


def company_groups() -> dict[str, tuple[str, ...]]:
    path = get_settings().config_dir / "company_groups.yml"
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = 0.0
    return _load_groups(stamp)


def _expand(company: str) -> set[str]:
    """A company plus every other name the same employer hires under.

    Works in both directions: claiming the parent hides the brands, and
    claiming a brand hides the parent and its siblings.
    """
    key = normalize_company(company)
    if not key:
        return set()
    out = {key}
    for parent, brands in company_groups().items():
        family = {normalize_company(parent), *(normalize_company(b) for b in brands)}
        if key in family:
            out |= family
    return {c for c in out if c}


def persona_employers(persona: Any) -> set[str]:
    """Normalized companies this persona's own history claims."""
    names: set[str] = set()
    for role in getattr(persona, "experience_ground_truth", None) or []:
        if isinstance(role, dict):
            company = str(role.get("company") or "").strip()
            if company:
                names |= _expand(company)
    return names


def conflict_reason(persona: Any, company: str) -> str | None:
    """Why this company is hidden for this persona, or None if it is not."""
    key = normalize_company(company)
    if not key:
        return None
    for role in getattr(persona, "experience_ground_truth", None) or []:
        if not isinstance(role, dict):
            continue
        claimed = str(role.get("company") or "").strip()
        if not claimed:
            continue
        if key in _expand(claimed):
            same = normalize_company(claimed) == key
            return (
                f"resume says {claimed}"
                if same
                else f"same employer as {claimed}"
            )
    return None
