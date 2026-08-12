from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from app.config import get_settings


@dataclass
class GateResult:
    ok: bool
    reason: str = ""


JUNIOR_RE = re.compile(
    r"\b(intern|internship|junior|associate|entry[\s-]level|graduate|new grad|"
    r"co-?op|apprentice)\b",
    re.I,
)


def check_seniority(title: str) -> GateResult:
    path = get_settings().config_dir / "seniority_rules.yml"
    rules: dict = {}
    if path.exists():
        rules = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    reject_patterns = rules.get("reject_title_patterns") or []
    for pat in reject_patterns:
        if re.search(pat, title or "", re.I):
            return GateResult(ok=False, reason="junior_title")

    if JUNIOR_RE.search(title or ""):
        # Allow "Senior Associate" style if senior/staff/principal present
        if re.search(r"\b(senior|staff|principal|lead|sr\.?)\b", title or "", re.I):
            return GateResult(ok=True)
        return GateResult(ok=False, reason="junior_title")
    return GateResult(ok=True)
