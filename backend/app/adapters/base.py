from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawListing:
    """Normalized listing from a board/ATS adapter."""

    source: str  # board name only — NEVER company
    company_name: str  # hiring company / end client
    job_title: str
    job_url: str
    location_raw: str | None = None
    description: str | None = None
    posted_at: str | None = None
    is_remote_flag_from_board: bool | None = None
    external_id: str | None = None
    canonical_url: str | None = None
    staffing_firm: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
