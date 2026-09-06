from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml

from app.config import get_settings

_SUFFIX_RE = re.compile(
    r"\b(inc|inc\.|llc|l\.l\.c\.|ltd|ltd\.|corp|corporation|co|co\.|company|plc|gmbh|ag)\b\.?",
    re.I,
)
_NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
_SPACE = re.compile(r"\s+")

# Bracketed decorations: "(Remote)", "[Contract]".
_BRACKETS_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
# Requisition ids: "req #12345", "job id 88231", "requisition: ABC-9".
_REQ_ID_RE = re.compile(r"\b(?:job\s*)?(?:id|req|requisition)\b\s*[:#-]?\s*[\w-]*", re.I)
_BARE_NUMBER_RE = re.compile(r"\b\d{3,}\b")
# Engagement wording that varies between re-postings of the same role.
_ENGAGEMENT_RE = re.compile(
    r"\b(?:100\s*%?\s*)?remote\b|\bonsite\b|\bon-site\b|\bhybrid\b|\bw2\b|\bc2c\b"
    r"|\bcorp\s*to\s*corp\b|\bcontract(?:or)?\b|\bfull[\s-]?time\b|\bpart[\s-]?time\b",
    re.I,
)
_TITLE_KEEP_RE = re.compile(r"[^a-z0-9+#]+")


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


def normalize_title(title: str) -> str:
    """Collapse a posting title so re-postings of the same role collide.

    Boards and CSV exports re-list one job with different decoration —
    "(Remote)", "- W2", a requisition id — so comparing raw titles misses most
    real duplicates.
    """
    text = (title or "").casefold()
    text = _BRACKETS_RE.sub(" ", text)
    text = _REQ_ID_RE.sub(" ", text)
    text = _BARE_NUMBER_RE.sub(" ", text)
    text = _ENGAGEMENT_RE.sub(" ", text)
    text = _TITLE_KEEP_RE.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def canonicalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = (parts.path or "/").rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))
