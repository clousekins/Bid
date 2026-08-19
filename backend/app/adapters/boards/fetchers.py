from __future__ import annotations

import httpx

from app.adapters.base import RawListing

USER_AGENT = "RemoteApplyBot/1.0 (+local; research)"


async def fetch_json(url: str, timeout: float = 30.0) -> dict | list:
    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


async def fetch_text(url: str, timeout: float = 30.0) -> tuple[int, str]:
    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        r = await client.get(url)
        return r.status_code, r.text


def _company_from(obj: dict, *keys: str) -> str:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, dict):
            v = v.get("name") or v.get("title")
        if v and str(v).strip():
            return str(v).strip()
    return ""


async def fetch_remoteok() -> list[RawListing]:
    data = await fetch_json("https://remoteok.com/api")
    out: list[RawListing] = []
    if not isinstance(data, list):
        return out
    for row in data:
        if not isinstance(row, dict) or not row.get("id") or not row.get("position"):
            continue
        company = _company_from(row, "company")
        if not company:
            continue
        loc = row.get("location") or ""
        tags = " ".join(row.get("tags") or [])
        desc = row.get("description") or ""
        url = row.get("url") or row.get("apply_url") or ""
        out.append(
            RawListing(
                source="Remote OK",
                company_name=company,
                job_title=str(row.get("position")),
                job_url=str(url),
                location_raw=str(loc) if loc else "Remote",
                description=f"{desc}\n{tags}",
                posted_at=str(row.get("date") or "") or None,
                is_remote_flag_from_board=True,
                external_id=str(row.get("id")),
                canonical_url=str(url) if url else None,
                raw=row,
            )
        )
    return out


async def fetch_remotive() -> list[RawListing]:
    """Pull multiple Remotive categories for a wider unique-company pool."""
    categories = [
        "",
        "software-dev",
        "data",
        "devops",
        "qa",
        "product",
        "design",
        "all-others",
    ]
    seen_ids: set[str] = set()
    out: list[RawListing] = []
    for cat in categories:
        url = "https://remotive.com/api/remote-jobs"
        if cat:
            url = f"{url}?category={cat}"
        try:
            data = await fetch_json(url)
        except Exception:
            continue
        jobs = data.get("jobs") if isinstance(data, dict) else None
        for row in jobs or []:
            ext = str(row.get("id") or "")
            if ext and ext in seen_ids:
                continue
            if ext:
                seen_ids.add(ext)
            company = _company_from(row, "company_name", "company")
            title = row.get("title") or ""
            if not company or not title:
                continue
            job_url = row.get("url") or ""
            out.append(
                RawListing(
                    source="Remotive",
                    company_name=company,
                    job_title=str(title),
                    job_url=str(job_url),
                    location_raw=str(row.get("candidate_required_location") or "Remote"),
                    description=str(row.get("description") or ""),
                    posted_at=str(row.get("publication_date") or "") or None,
                    is_remote_flag_from_board=True,
                    external_id=ext or None,
                    canonical_url=str(job_url) or None,
                    raw=row,
                )
            )
    return out


async def fetch_jobicy(count: int = 100) -> list[RawListing]:
    data = await fetch_json(f"https://jobicy.com/api/v2/remote-jobs?count={count}")
    jobs = data.get("jobs") if isinstance(data, dict) else None
    out: list[RawListing] = []
    for row in jobs or []:
        company = _company_from(row, "companyName", "company")
        title = row.get("jobTitle") or ""
        if not company or not title:
            continue
        url = row.get("url") or ""
        out.append(
            RawListing(
                source="Jobicy",
                company_name=company,
                job_title=str(title),
                job_url=str(url),
                location_raw=str(row.get("jobGeo") or "Remote"),
                description=str(row.get("jobDescription") or ""),
                posted_at=str(row.get("pubDate") or "") or None,
                is_remote_flag_from_board=True,
                external_id=str(row.get("id")),
                canonical_url=str(url) or None,
                raw=row,
            )
        )
    return out


async def fetch_arbeitnow() -> list[RawListing]:
    data = await fetch_json("https://www.arbeitnow.com/api/job-board-api")
    jobs = data.get("data") if isinstance(data, dict) else None
    out: list[RawListing] = []
    for row in jobs or []:
        company = _company_from(row, "company_name", "company")
        title = row.get("title") or ""
        if not company or not title:
            continue
        if not row.get("remote"):
            continue
        url = row.get("url") or ""
        loc = row.get("location") or "Remote"
        out.append(
            RawListing(
                source="Arbeitnow",
                company_name=company,
                job_title=str(title),
                job_url=str(url),
                location_raw=str(loc),
                description=str(row.get("description") or ""),
                posted_at=str(row.get("created_at") or "") or None,
                is_remote_flag_from_board=True,
                external_id=str(row.get("slug") or row.get("url")),
                canonical_url=str(url) or None,
                raw=row,
            )
        )
    return out


async def fetch_himalayas() -> list[RawListing]:
    try:
        data = await fetch_json("https://himalayas.app/jobs/api?limit=100")
    except Exception:
        return []
    jobs = data if isinstance(data, list) else (data.get("jobs") or data.get("data") or [])
    out: list[RawListing] = []
    for row in jobs or []:
        if not isinstance(row, dict):
            continue
        company = _company_from(row, "companyName", "company", "company_name")
        title = row.get("title") or row.get("jobTitle") or ""
        if not company or not title:
            continue
        url = row.get("applicationLink") or row.get("url") or row.get("guid") or ""
        out.append(
            RawListing(
                source="Himalayas",
                company_name=company,
                job_title=str(title),
                job_url=str(url),
                location_raw=str(row.get("location") or row.get("jobLocation") or "Remote"),
                description=str(row.get("description") or row.get("excerpt") or ""),
                is_remote_flag_from_board=True,
                external_id=str(row.get("guid") or row.get("id") or url),
                canonical_url=str(url) or None,
                raw=row,
            )
        )
    return out


async def fetch_weworkremotely() -> list[RawListing]:
    import feedparser

    feeds = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "https://weworkremotely.com/categories/remote-data-jobs.rss",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
    ]
    out: list[RawListing] = []
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        for feed_url in feeds:
            try:
                r = await client.get(feed_url)
                r.raise_for_status()
            except Exception:
                continue
            parsed = feedparser.parse(r.text)
            for e in parsed.entries:
                title_full = e.get("title") or ""
                company = ""
                title = title_full
                if ":" in title_full:
                    company, title = [p.strip() for p in title_full.split(":", 1)]
                link = e.get("link") or ""
                summary = e.get("summary") or e.get("description") or ""
                if not company or not title:
                    continue
                out.append(
                    RawListing(
                        source="We Work Remotely",
                        company_name=company,
                        job_title=title,
                        job_url=str(link),
                        location_raw="Remote",
                        description=str(summary),
                        is_remote_flag_from_board=True,
                        external_id=str(e.get("id") or link),
                        canonical_url=str(link) or None,
                        raw={"title": title_full},
                    )
                )
    return out


async def fetch_greenhouse_board(board_token: str) -> list[RawListing]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    data = await fetch_json(url)
    jobs = data.get("jobs") if isinstance(data, dict) else None
    out: list[RawListing] = []
    company_fallback = board_token.replace("-", " ").title()
    for row in jobs or []:
        title = row.get("title") or ""
        absolute = row.get("absolute_url") or ""
        loc_objs = row.get("location") or {}
        loc = loc_objs.get("name") if isinstance(loc_objs, dict) else str(loc_objs or "")
        content = row.get("content") or ""
        company = company_fallback
        if not title:
            continue
        out.append(
            RawListing(
                source="Greenhouse ATS",
                company_name=company,
                job_title=str(title),
                job_url=str(absolute),
                location_raw=str(loc) or None,
                description=str(content),
                is_remote_flag_from_board=("remote" in (loc or "").lower())
                or ("remote" in (title or "").lower())
                or ("remote" in (content or "").lower()),
                external_id=str(row.get("id")),
                canonical_url=str(absolute) or None,
                raw=row,
            )
        )
    return out


async def fetch_lever_board(company_slug: str) -> list[RawListing]:
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
    data = await fetch_json(url)
    rows = data if isinstance(data, list) else []
    out: list[RawListing] = []
    company = company_slug.replace("-", " ").title()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = row.get("text") or row.get("title") or ""
        job_url = row.get("hostedUrl") or row.get("applyUrl") or ""
        if not title or not job_url:
            continue
        cats = row.get("categories") or {}
        loc = ""
        if isinstance(cats, dict):
            loc = cats.get("location") or cats.get("commitment") or ""
        desc = row.get("descriptionPlain") or row.get("description") or ""
        lists = row.get("lists") or []
        if isinstance(lists, list):
            for block in lists:
                if isinstance(block, dict):
                    desc += "\n" + str(block.get("text") or "")
        remote = ("remote" in (loc or "").lower()) or ("remote" in (title or "").lower())
        out.append(
            RawListing(
                source="Lever ATS",
                company_name=company,
                job_title=str(title),
                job_url=str(job_url),
                location_raw=str(loc) if loc else "Remote",
                description=str(desc),
                posted_at=str(row.get("createdAt") or "") or None,
                is_remote_flag_from_board=remote or True,
                external_id=str(row.get("id") or job_url),
                canonical_url=str(job_url),
                raw=row,
            )
        )
    return out


async def fetch_ashby_board(board_name: str) -> list[RawListing]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board_name}?includeCompensation=true"
    data = await fetch_json(url)
    jobs = data.get("jobs") if isinstance(data, dict) else None
    out: list[RawListing] = []
    company = board_name.replace("-", " ").title()
    for row in jobs or []:
        if not isinstance(row, dict):
            continue
        title = row.get("title") or ""
        job_url = row.get("jobUrl") or row.get("applyUrl") or ""
        if not title or not job_url:
            continue
        loc = row.get("location") or ""
        if isinstance(loc, dict):
            loc = loc.get("name") or ""
        desc = row.get("descriptionPlain") or row.get("descriptionHtml") or ""
        is_remote = bool(row.get("isRemote")) or ("remote" in str(loc).lower())
        out.append(
            RawListing(
                source="Ashby ATS",
                company_name=company,
                job_title=str(title),
                job_url=str(job_url),
                location_raw=str(loc) if loc else ("Remote" if is_remote else None),
                description=str(desc),
                posted_at=str(row.get("publishedAt") or "") or None,
                is_remote_flag_from_board=is_remote,
                external_id=str(row.get("id") or job_url),
                canonical_url=str(job_url),
                raw=row,
            )
        )
    return out


async def fetch_themuse(pages: int = 3) -> list[RawListing]:
    """The Muse public jobs API — Software Engineering / Data Science categories."""
    categories = ["Software Engineering", "Data Science", "UX", "Design and UX"]
    out: list[RawListing] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        for cat in categories:
            try:
                async with httpx.AsyncClient(
                    timeout=30.0,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    follow_redirects=True,
                ) as client:
                    r = await client.get(
                        "https://www.themuse.com/api/public/jobs",
                        params={"page": page, "descending": "true", "category": cat},
                    )
                    r.raise_for_status()
                    data = r.json()
            except Exception:
                continue
            for row in data.get("results") or []:
                if not isinstance(row, dict):
                    continue
                ext = str(row.get("id") or "")
                if ext and ext in seen:
                    continue
                if ext:
                    seen.add(ext)
                company = _company_from(row, "company")
                title = row.get("name") or ""
                if not company or not title:
                    continue
                locs = row.get("locations") or []
                loc_names = []
                for loc in locs:
                    if isinstance(loc, dict) and loc.get("name"):
                        loc_names.append(str(loc["name"]))
                loc_raw = ", ".join(loc_names) if loc_names else "Remote"
                joined = " ".join(loc_names).lower()
                if loc_names and not any(
                    x in joined for x in ("united states", "usa", "flexible", "remote", "anywhere")
                ):
                    continue
                refs = row.get("refs") or {}
                job_url = (refs.get("landing_page") if isinstance(refs, dict) else None) or ""
                if not job_url:
                    continue
                out.append(
                    RawListing(
                        source="The Muse",
                        company_name=company,
                        job_title=str(title),
                        job_url=str(job_url),
                        location_raw=loc_raw,
                        description=str(row.get("contents") or ""),
                        posted_at=str(row.get("publication_date") or "") or None,
                        is_remote_flag_from_board=("remote" in loc_raw.lower())
                        or ("flexible" in loc_raw.lower()),
                        external_id=ext or None,
                        canonical_url=str(job_url),
                        raw=row,
                    )
                )
    return out
