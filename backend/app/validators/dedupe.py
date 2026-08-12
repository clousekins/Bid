from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.config import get_settings

_SUFFIX_RE = re.compile(
    r"\b(inc|inc\.|llc|l\.l\.c\.|ltd|ltd\.|corp|corporation|co|co\.|company|plc|gmbh|ag)\b\.?",
    re.I,
)
_NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
_SPACE = re.compile(r"\s+")


def _load_aliases() -> dict[str, str]:
    path = get_settings().config_dir / "company_aliases.yml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    aliases = data.get("aliases") or data
    if not isinstance(aliases, dict):
        return {}
    return {str(k).lower().strip(): str(v).strip() for k, v in aliases.items()}


def normalize_company(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    aliases = _load_aliases()
    key = raw.lower()
    if key in aliases:
        raw = aliases[key]
    cleaned = _SUFFIX_RE.sub("", raw.lower())
    cleaned = _NON_ALNUM.sub(" ", cleaned)
    cleaned = _SPACE.sub(" ", cleaned).strip()
    return cleaned or raw.lower().strip()
