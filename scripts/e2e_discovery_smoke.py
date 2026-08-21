from __future__ import annotations

import asyncio
import json
import time
import urllib.request

from app.adapters.base import RawListing
from app.adapters.boards.fetchers import greenhouse_board_company_name
from app.config import get_settings
from app.services.company_discovery import search_provider_status
from app.services.discover import _passes_prefilter, load_discovery_settings


def api(path: str, method: str = "GET", body: dict | None = None):
    s = get_settings()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:8000{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {s.access_token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


async def checks() -> None:
    get_settings.cache_clear()
    print("provider", search_provider_status())
    print("health", api("/api/health"))
    st = api("/api/jobs/meta/stats")
    print("stats", {k: st.get(k) for k in ("total", "max_seq", "min_seq")})
    print("ats", st.get("ats"))
    print("settings.discovery", api("/api/settings").get("company_discovery"))
    cfg = load_discovery_settings(mode="standard")
    fresh = load_discovery_settings(mode="fresh")
    print(
        "cfg.standard",
        {k: cfg[k] for k in ("posted_within_days", "boards_per_run", "insert_limit", "mode")},
    )
    print(
        "cfg.fresh",
        {
            k: fresh[k]
            for k in (
                "posted_within_days",
                "boards_per_run",
                "insert_limit",
                "mode",
                "require_posted_at",
            )
        },
    )
    print("stripe_name", await greenhouse_board_company_name("stripe"))
    ok, reason = _passes_prefilter(
        RawListing(
            source="t",
            company_name="X",
            job_title="Senior Software Engineer",
            job_url="https://x.com/1",
            location_raw="Remote",
            description="Build APIs",
        )
    )
    print("bare_remote", ok, reason)
    ok2, reason2 = _passes_prefilter(
        RawListing(
            source="t",
            company_name="X",
            job_title="Senior Software Engineer",
            job_url="https://x.com/2",
            location_raw="Hybrid SF",
            description="Office 3 days",
        )
    )
    print("hybrid", ok2, reason2)

    job = api("/api/sync/discover-companies", "POST", {"persona_id": "phong_v_cao"})
    print("enqueued_companies", job)
    for i in range(50):
        time.sleep(3)
        bg = api(f"/api/jobs-bg/{job['job_id']}")
        prog = bg.get("progress") or {}
        if isinstance(prog, str):
            prog = json.loads(prog)
        print(
            f"poll{i}",
            bg.get("status"),
            {
                k: prog.get(k)
                for k in ("provider", "added", "seen", "ok", "message", "queries_run", "pages_ok")
                if prog.get(k) is not None
            },
            "err=",
            (bg.get("error") or "")[:160],
        )
        if bg.get("status") in {"completed", "failed", "cancelled"}:
            print("errors", (prog.get("errors") or [])[:5])
            print("boards_sample", (prog.get("boards") or [])[:8])
            break

    # short discover fresh enqueue + one poll (don't wait full run)
    fresh_job = api("/api/sync/discover-fresh", "POST", {"persona_id": "phong_v_cao"})
    print("enqueued_fresh", fresh_job)
    time.sleep(5)
    bg2 = api(f"/api/jobs-bg/{fresh_job['job_id']}")
    prog2 = bg2.get("progress") or {}
    if isinstance(prog2, str):
        prog2 = json.loads(prog2)
    print(
        "fresh_status",
        bg2.get("status"),
        {
            k: prog2.get(k)
            for k in (
                "mode",
                "discovered_raw",
                "after_filters",
                "inserted",
                "jd_ready",
                "gated",
                "done",
                "total",
            )
            if prog2.get(k) is not None
        },
    )


if __name__ == "__main__":
    asyncio.run(checks())
