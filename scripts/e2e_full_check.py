"""Full E2E smoke: health, catalog UI API, discover companies, discover fresh."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from app.config import get_settings


def api(path: str, method: str = "GET", body: dict | None = None, timeout: float = 120):
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code} {path}: {detail}") from e


def poll_bg(job_id: int, *, label: str, max_polls: int = 100, sleep_s: float = 3.0) -> dict:
    last = {}
    for i in range(max_polls):
        time.sleep(sleep_s)
        bg = api(f"/api/jobs-bg/{job_id}")
        p = bg.get("progress") or {}
        if isinstance(p, str):
            p = json.loads(p)
        keys = (
            "phase",
            "mode",
            "provider",
            "fallback_from",
            "added",
            "seen",
            "probed",
            "queries_run",
            "pages_ok",
            "discovered_raw",
            "after_filters",
            "inserted",
            "jd_ready",
            "gated",
            "skipped",
            "boards_done",
            "boards_total",
            "done",
            "total",
            "message",
        )
        slim = {k: p.get(k) for k in keys if p.get(k) is not None}
        err = (bg.get("error") or "")[:200]
        print(f"[{label}] poll{i} {bg.get('status')} {slim} err={err!r}")
        last = {"status": bg.get("status"), "progress": p, "error": bg.get("error")}
        if bg.get("status") in {"completed", "failed", "cancelled"}:
            return last
    raise SystemExit(f"{label} timed out after {max_polls} polls")


def main() -> None:
    get_settings.cache_clear()
    print("=== 1. health ===")
    print(api("/api/health"))

    print("=== 2. stats + catalog summary ===")
    st = api("/api/jobs/meta/stats")
    ats = st.get("ats") or {}
    print(
        {
            "jobs_total": st.get("total"),
            "max_seq": st.get("max_seq"),
            "ats_total": ats.get("total"),
            "ats_checked": ats.get("checked"),
            "ats_pending": ats.get("pending"),
            "ats_with_jobs": ats.get("with_jobs"),
            "by_status": ats.get("by_status"),
        }
    )

    print("=== 3. company catalog boards API (UI) ===")
    boards = api("/api/sync/ats-catalog/boards?limit=8&status=searched")
    print("searched_total", boards.get("total"))
    for b in (boards.get("boards") or [])[:5]:
        print(
            " ",
            b.get("status"),
            b.get("source"),
            b.get("company_name") or b.get("token"),
            "jobs=",
            b.get("jobs_found"),
        )
    waiting = api("/api/sync/ats-catalog/boards?limit=5&status=waiting")
    print("waiting_total", waiting.get("total"))
    ok = api("/api/sync/ats-catalog/boards?limit=5&status=ok&q=stripe")
    print("search_stripe", ok.get("total"), (ok.get("boards") or [])[:2])

    print("=== 3b. personas + fetch domains ===")
    plist = api("/api/settings/personas")
    persona_ids = [p.get("id") for p in (plist.get("personas") or []) if isinstance(p, dict)]
    print("persona_ids", persona_ids, "active", plist.get("active"))
    if "albert_liu" not in persona_ids:
        raise SystemExit("albert_liu persona missing from settings/personas")
    sites = api("/api/sync/fetch-sites")
    domain_ids = [d.get("id") for d in (sites.get("domains") or [])]
    print(
        "fetch_sites",
        {
            "sites": len(sites.get("sites") or []),
            "windows": [w.get("id") for w in (sites.get("windows") or [])],
            "domains": domain_ids,
        },
    )
    for need in ("ai", "de", "da", "ds", "fs", "sf"):
        if need not in domain_ids:
            raise SystemExit(f"missing domain in fetch-sites: {need}")

    print("=== 4. cancel any stuck discover_* jobs ===")
    for jid in range(200, 80, -1):
        try:
            bg = api(f"/api/jobs-bg/{jid}")
            if bg.get("status") in {"queued", "running"} and "discover" in str(bg.get("kind") or ""):
                print("cancelling", jid, bg.get("kind"), bg.get("status"))
                api(f"/api/jobs-bg/{jid}/cancel", "POST", {})
        except Exception:
            pass
    # Give worker a moment to release kind locks after cancel
    time.sleep(2)

    print("=== 5. discover companies ===")
    before = ats.get("total")
    job = api("/api/sync/discover-companies", "POST", {"persona_id": "phong_v_cao"})
    print("enqueued", job)
    res = poll_bg(job["job_id"], label="companies", max_polls=120, sleep_s=3.0)
    if res["status"] != "completed":
        raise SystemExit(f"discover_companies ended as {res['status']}: {res.get('error')}")
    after_stats = api("/api/jobs/meta/stats").get("ats") or {}
    print(
        "companies_result",
        {
            "status": res["status"],
            "added": (res["progress"] or {}).get("added"),
            "seen": (res["progress"] or {}).get("seen"),
            "provider": (res["progress"] or {}).get("provider"),
            "fallback_from": (res["progress"] or {}).get("fallback_from"),
            "ats_before": before,
            "ats_after": after_stats.get("total"),
        },
    )

    print("=== 6. discover fresh (24h) ===")
    jobs_before = api("/api/jobs/meta/stats").get("total")
    fresh = api("/api/sync/discover-fresh", "POST", {"persona_id": "phong_v_cao"})
    print("enqueued", fresh)
    fres = poll_bg(fresh["job_id"], label="fresh", max_polls=100)
    if fres["status"] != "completed":
        raise SystemExit(f"discover_fresh ended as {fres['status']}: {fres.get('error')}")
    jobs_after = api("/api/jobs/meta/stats").get("total")
    fp = fres["progress"] or {}
    print(
        "fresh_result",
        {
            "status": fres["status"],
            "raw": fp.get("discovered_raw"),
            "filtered": fp.get("after_filters"),
            "inserted": fp.get("inserted"),
            "jd_ready": fp.get("jd_ready"),
            "gated": fp.get("gated"),
            "skipped": fp.get("skipped"),
            "jobs_before": jobs_before,
            "jobs_after": jobs_after,
            "boards": f"{fp.get('boards_done')}/{fp.get('boards_total')}",
        },
    )

    print("=== 7. discover-site remoteok + domains DE/AI/SF ===")
    site_job = api(
        "/api/sync/discover-site",
        "POST",
        {
            "persona_id": "albert_liu",
            "site": "remoteok",
            "window": "7d",
            "max_jobs": 40,
            "domains": ["de", "ai", "sf"],
        },
    )
    print("enqueued", site_job)
    sres = poll_bg(site_job["job_id"], label="site", max_polls=80)
    if sres["status"] != "completed":
        raise SystemExit(f"discover_site ended as {sres['status']}: {sres.get('error')}")
    sp = sres["progress"] or {}
    print(
        "site_result",
        {
            "status": sres["status"],
            "domains": sp.get("domains") or site_job.get("domains"),
            "raw": sp.get("discovered_raw"),
            "filtered": sp.get("after_filters"),
            "inserted": sp.get("inserted"),
            "drops": (sp.get("prefilter_drops") or {}).get("domain_mismatch"),
        },
    )
    sample = api("/api/jobs?page=1&page_size=8&profile=albert_liu")
    jobs = sample.get("jobs") or sample.get("items") or []
    print(
        "jobs_sample_domains",
        [
            {"seq": j.get("seq_no"), "title": (j.get("title") or "")[:50], "domains": j.get("domains")}
            for j in jobs[:8]
        ],
    )

    print("=== 8. catalog after run ===")
    final = api("/api/sync/ats-catalog/boards?limit=5&status=ok")
    print("ok_boards", final.get("total"), [(b.get("company_name") or b.get("token")) for b in final.get("boards") or []])
    print("E2E_OK")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as e:
        raise SystemExit(f"API unreachable: {e}") from e
