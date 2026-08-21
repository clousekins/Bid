"""E2E: enqueue discover-fresh and poll until done."""
from __future__ import annotations

import json
import time
import urllib.request

from app.config import get_settings


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


def main() -> None:
    get_settings.cache_clear()
    before = api("/api/jobs/meta/stats")
    print(
        "before",
        {
            "total": before.get("total"),
            "ats": (before.get("ats") or {}).get("total"),
            "pending": (before.get("ats") or {}).get("pending"),
        },
    )
    job = api("/api/sync/discover-fresh", "POST", {"persona_id": "phong_v_cao"})
    print("enqueued", job)
    jid = job["job_id"]
    for i in range(120):
        time.sleep(3)
        bg = api(f"/api/jobs-bg/{jid}")
        prog = bg.get("progress") or {}
        if isinstance(prog, str):
            prog = json.loads(prog)
        keys = (
            "mode",
            "posted_within_days",
            "discovered_raw",
            "after_filters",
            "inserted",
            "jd_ready",
            "needs_scan",
            "gated",
            "skipped",
            "employer_resolved",
            "ats_this_run",
            "ats_pending",
            "auto_scan_queued",
            "done",
            "total",
            "message",
        )
        slim = {k: prog.get(k) for k in keys if prog.get(k) is not None}
        print(f"poll{i}", bg.get("status"), slim, "err=", (bg.get("error") or "")[:120])
        if bg.get("status") in {"completed", "failed", "cancelled"}:
            after = api("/api/jobs/meta/stats")
            print(
                "after",
                {
                    "total": after.get("total"),
                    "ats": (after.get("ats") or {}).get("total"),
                    "pending": (after.get("ats") or {}).get("pending"),
                    "by_status": (after.get("ats") or {}).get("by_status"),
                },
            )
            return
    raise SystemExit("timed out waiting for discover-fresh")


if __name__ == "__main__":
    main()
