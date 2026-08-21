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
    # cancel any stuck jobs
    for jid in (73, 74):
        try:
            api(f"/api/jobs-bg/{jid}/cancel", "POST", {})
            print("cancelled", jid)
        except Exception as e:
            print("cancel", jid, e)

    before = api("/api/jobs/meta/stats").get("ats") or {}
    print("ats_before", {k: before.get(k) for k in ("total", "pending", "checked")})

    job = api("/api/sync/discover-companies", "POST", {"persona_id": "phong_v_cao"})
    print("enqueued", job)
    for i in range(90):
        time.sleep(2)
        bg = api(f"/api/jobs-bg/{job['job_id']}")
        prog = bg.get("progress") or {}
        if isinstance(prog, str):
            prog = json.loads(prog)
        print(
            f"poll{i}",
            bg.get("status"),
            {
                k: prog.get(k)
                for k in (
                    "provider",
                    "mode",
                    "probed",
                    "candidates",
                    "added",
                    "seen",
                    "fallback_from",
                    "message",
                    "done",
                    "total",
                )
                if prog.get(k) is not None
            },
        )
        if bg.get("status") in {"completed", "failed", "cancelled"}:
            print("errors", (prog.get("errors") or [])[:3])
            print("boards", (prog.get("boards") or [])[:6])
            break

    after = api("/api/jobs/meta/stats").get("ats") or {}
    print("ats_after", {k: after.get(k) for k in ("total", "pending", "checked")})


if __name__ == "__main__":
    main()
