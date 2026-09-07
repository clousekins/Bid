"""CSV record of what a fetch found, written under data/fetches/.

A run that reads 600 listings and inserts 40 has still read 600, and the 560
it set aside are the ones worth a second look by hand. The table only ever
shows what survived every gate; this file is the whole catch, with `in_app`
saying which rows reached the table and why the rest did not.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

# The shape the exported sheets already use, plus the app's own numbering.
FETCH_CSV_COLUMNS = [
    "seq_no",
    "company",
    "title",
    "url",
    "location",
    "posted_at",
    "source",
    "external_id",
    "domains",
    "employment_type",
    "staffing_firm",
    "in_app",
    "description",
]


def fetch_csv_dir() -> Path:
    """data/fetches — one file per run, so a fetch never overwrites the last."""
    path = get_settings().root_dir / "data" / "fetches"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _norm_url(value: Any) -> str:
    return str(value or "").strip().lower().rstrip("/")


def _in_app(item: dict[str, Any] | None) -> str:
    """What became of one row: its status in the app, or why it was left out."""
    if not item:
        # The insert loop stops once the window's quota is filled, so the tail
        # of a large catch is never offered to it. "skipped" would be a lie.
        return "not_attempted"
    if item.get("ok"):
        return str(item.get("status") or "added")
    return str(item.get("reason") or "skipped")


def write_fetch_csv(
    *,
    site: str,
    window: str = "",
    listings: list[dict[str, Any]],
    outcomes: list[dict[str, Any]] | None = None,
) -> Path | None:
    """Write one row per job found. Returns the path, or None for an empty run.

    `outcomes` are the insert results, matched back to their listing by URL —
    the only field both sides are sure to share.
    """
    if not listings:
        return None
    by_url: dict[str, dict[str, Any]] = {}
    for item in outcomes or []:
        key = _norm_url(item.get("url"))
        if key:
            by_url[key] = item

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = "-".join(p for p in (_slug(site) or "fetch", _slug(window), stamp) if p)
    directory = fetch_csv_dir()
    path = directory / f"{name}.csv"
    # Two runs finishing inside the same second would otherwise share a name,
    # and the second would silently take the first one's place.
    suffix = 2
    while path.exists():
        path = directory / f"{name}-{suffix}.csv"
        suffix += 1

    # utf-8-sig: these get opened in Excel, which reads a plain UTF-8 file as
    # the local codepage and mangles every accented company name.
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FETCH_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for listing in listings:
            item = by_url.get(_norm_url(listing.get("url")))
            writer.writerow(
                {
                    "seq_no": (item or {}).get("seq_no") or "",
                    "company": listing.get("company") or "",
                    "title": listing.get("title") or "",
                    "url": listing.get("url") or "",
                    "location": listing.get("location") or "",
                    "posted_at": listing.get("posted_at") or "",
                    "source": listing.get("source") or "",
                    "external_id": listing.get("external_id") or "",
                    "domains": listing.get("domains") or "",
                    "employment_type": listing.get("employment_type") or "",
                    "staffing_firm": listing.get("staffing_firm") or "",
                    "in_app": _in_app(item),
                    "description": listing.get("description") or "",
                }
            )
    log.info("fetch csv: %s rows -> %s", len(listings), path)
    return path
