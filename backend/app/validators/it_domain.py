from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from app.config import get_settings


@dataclass
class GateResult:
    ok: bool
    reason: str = ""


DEFAULT_KEYWORDS = [
    r"software",
    r"engineer",
    r"developer",
    r"machine learning",
    r"\bml\b",
    r"\bai\b",
    r"data scientist",
    r"devops",
    r"sre",
    r"platform",
    r"backend",
    r"frontend",
    r"full[\s-]?stack",
    r"infrastructure",
    r"security",
    r"cloud",
    r"llm",
    r"gpu",
]


def check_it_domain(title: str, description: str | None = None) -> GateResult:
    path = get_settings().config_dir / "it_title_keywords.yml"
    patterns = list(DEFAULT_KEYWORDS)
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        extra = data.get("keywords") or data.get("title_keywords") or []
        patterns.extend(str(x) for x in extra)

    text = f"{title or ''}\n{(description or '')[:2000]}"
    for pat in patterns:
        if re.search(pat, text, re.I):
            return GateResult(ok=True)
    return GateResult(ok=False, reason="not_it_domain")
