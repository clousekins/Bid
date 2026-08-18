from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


@lru_cache
def load_job_sites() -> dict[str, Any]:
    return _load_yaml(get_settings().config_dir / "job_sites.yml")


def reload_configs() -> None:
    load_job_sites.cache_clear()
