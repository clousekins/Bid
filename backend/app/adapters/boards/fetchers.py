from __future__ import annotations

import asyncio
import json
import html
import re
import time
import logging
from html.parser import HTMLParser
from urllib.parse import quote

import httpx

from app.adapters.base import RawListing

log = logging.getLogger(__name__)

USER_AGENT = "RemoteApplyBot/1.0 (+local; research)"


class _StripTags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def get_text(self) -> str:
        return "".join(self.parts)


def _html_to_text(raw: str) -> str:
    text = html.unescape(raw or "")
    parser = _StripTags()
    try:
        parser.feed(text)
        parser.close()
        text = parser.get_text()
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def fetch_json(url: str, timeout: float = 20.0, *, retries: int = 2) -> dict | list:
    delay = 0.6
    last_exc: Exception | None = None
    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        for attempt in range(max(1, retries)):
            try:
                r = await client.get(url)
                if r.status_code == 429 and attempt < retries - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 8.0)
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= retries - 1:
                    break
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)
    assert last_exc is not None
    raise last_exc



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


async def greenhouse_board_company_name(board_token: str) -> str:
    """Real employer name from Greenhouse board metadata (not the ATS product name)."""
    token = (board_token or "").strip()
    fallback = token.replace("-", " ").title() or "Unknown"
    if not token:
        return fallback
    try:
        data = await fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}")
    except Exception:  # noqa: BLE001
        return fallback
    if isinstance(data, dict):
        name = str(data.get("name") or "").strip()
        if name:
            return name
    return fallback


async def fetch_greenhouse_board(board_token: str) -> list[RawListing]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    data = await fetch_json(url)
    jobs = data.get("jobs") if isinstance(data, dict) else None
    out: list[RawListing] = []
    company = await greenhouse_board_company_name(board_token)
    for row in jobs or []:
        title = row.get("title") or ""
        absolute = row.get("absolute_url") or ""
        loc_objs = row.get("location") or {}
        loc = loc_objs.get("name") if isinstance(loc_objs, dict) else str(loc_objs or "")
        content = row.get("content") or ""
        if not title:
            continue
        posted = row.get("updated_at") or row.get("created_at")
        # Prefer per-job company metadata when present (rare staffing boards)
        job_company = _company_from(row, "company", "employer") or company
        out.append(
            RawListing(
                source="Greenhouse ATS",
                company_name=job_company,
                job_title=str(title),
                job_url=str(absolute),
                location_raw=str(loc) or None,
                description=_html_to_text(str(content)) if content else str(content),
                posted_at=str(posted) if posted else None,
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
        # Lever sometimes embeds employer in additionalPlain / workplaceType
        job_company = (
            _company_from(row, "company", "organization")
            or (cats.get("team") if isinstance(cats, dict) else None)
            or company
        )
        if isinstance(job_company, str) and job_company.strip().lower() in {
            "engineering",
            "product",
            "design",
            "sales",
            "marketing",
            "operations",
        }:
            job_company = company
        out.append(
            RawListing(
                source="Lever ATS",
                company_name=str(job_company).strip() or company,
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
    if isinstance(data, dict):
        company = (
            str(
                data.get("organizationName")
                or data.get("companyName")
                or data.get("name")
                or company
            ).strip()
            or company
        )
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
        if row.get("descriptionHtml") and not row.get("descriptionPlain"):
            desc = _html_to_text(str(desc))
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


_HN_TITLE_RE = re.compile(r"^Ask HN: Who is hiring\?\s*\(", re.I)
_HN_ENG_RE = re.compile(
    r"\b(software|engineer|engineering|developer|frontend|front[\s-]?end|"
    r"backend|back[\s-]?end|full[\s-]?stack|devops|sre|platform|ml|machine learning|"
    r"data engineer|ai engineer|typescript|react|python|golang|rust)\b",
    re.I,
)
_HN_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.I)


def parse_hn_hiring_comment(text: str, *, story_id: str, comment_id: str) -> RawListing | None:
    """Parse a top-level HN 'Who is hiring?' comment into a listing."""
    plain = _html_to_text(text)
    if not plain or len(plain) < 20:
        return None
    first = plain.split("|")[0].strip() if "|" in plain else plain.split(".")[0].strip()
    parts = [p.strip() for p in plain.split("|") if p.strip()]
    if len(parts) < 2:
        return None
    company = parts[0]
    # Strip trailing location in parentheses from company field
    company = re.sub(r"\s*\([^)]*\)\s*$", "", company).strip()
    title = parts[1]
    location = parts[2] if len(parts) > 2 else "Remote"
    blob = plain.lower()
    if not _HN_ENG_RE.search(title) and not _HN_ENG_RE.search(plain[:240]):
        return None
    from app.validators.remote_us import us_only

    if us_only() and "remote" not in blob and "us only" not in blob and "united states" not in blob:
        # Keep US onsite? No — we want remote US. Allow if location says USA/US remote.
        # Skipped entirely when intake is open to every country; the pipeline's
        # own gate still enforces remote-ness.
        if not re.search(r"\b(usa|u\.s\.|united states)\b", blob):
            return None
    if any(x in blob for x in ("onsite only", "on-site only", "hybrid only")) and "remote" not in blob:
        return None
    urls = _HN_URL_RE.findall(text) or _HN_URL_RE.findall(plain)
    job_url = urls[0] if urls else f"https://news.ycombinator.com/item?id={comment_id}"
    return RawListing(
        source="HN Who is Hiring",
        company_name=company[:255],
        job_title=title[:512],
        job_url=job_url,
        location_raw=location[:512] if location else "Remote",
        description=plain[:6000],
        posted_at=None,
        is_remote_flag_from_board=("remote" in blob),
        external_id=f"hn-{story_id}-{comment_id}",
        canonical_url=job_url,
        raw={"story_id": story_id, "comment_id": comment_id},
    )


async def fetch_hn_whoishiring() -> list[RawListing]:
    """Latest monthly Ask HN: Who is hiring? thread via Algolia (public)."""
    data = await fetch_json(
        "https://hn.algolia.com/api/v1/search_by_date"
        "?query=Ask%20HN%3A%20Who%20is%20hiring%3F&tags=story&hitsPerPage=30"
    )
    hits = data.get("hits") if isinstance(data, dict) else None
    story = None
    for hit in hits or []:
        title = str((hit or {}).get("title") or "")
        if _HN_TITLE_RE.search(title):
            story = hit
            break
    if not story:
        return []
    story_id = str(story.get("objectID") or "")
    if not story_id:
        return []
    item = await fetch_json(f"https://hn.algolia.com/api/v1/items/{story_id}")
    children = item.get("children") if isinstance(item, dict) else None
    out: list[RawListing] = []
    seen: set[str] = set()
    for child in children or []:
        if not isinstance(child, dict):
            continue
        if child.get("type") and child.get("type") != "comment":
            continue
        cid = str(child.get("id") or "")
        text = child.get("text") or ""
        listing = parse_hn_hiring_comment(str(text), story_id=story_id, comment_id=cid or "0")
        if not listing:
            continue
        key = (listing.company_name.lower(), listing.job_title.lower())
        if key in seen:
            continue
        seen.add(key)
        # Prefer story created_at as posted_at for freshness filtering
        listing.posted_at = str(story.get("created_at") or "") or None
        out.append(listing)
        if len(out) >= 200:
            break
    return out


async def fetch_workable_board(account: str) -> list[RawListing]:
    """Workable public widget/jobs JSON (no auth)."""
    account = (account or "").strip()
    company = account.replace("-", " ").title()
    data: dict | list | None = None
    for url in (
        f"https://apply.workable.com/api/v1/widget/accounts/{account}",
        f"https://apply.workable.com/api/v3/accounts/{account}/jobs?details=true",
    ):
        try:
            data = await fetch_json(url)
            break
        except Exception:  # noqa: BLE001
            continue
    if data is None:
        return []
    out: list[RawListing] = []
    if isinstance(data, dict):
        company = str(data.get("name") or data.get("company") or company).strip() or company
        jobs = data.get("jobs") or data.get("results") or []
    else:
        jobs = data if isinstance(data, list) else []
    for row in jobs or []:
        if not isinstance(row, dict):
            continue
        title = row.get("title") or row.get("shortcode") or ""
        job_url = (
            row.get("url")
            or row.get("application_url")
            or row.get("shortlink")
            or ""
        )
        if not job_url and row.get("shortcode"):
            job_url = f"https://apply.workable.com/{account}/j/{row.get('shortcode')}/"
        if not title or not job_url:
            continue
        loc = row.get("location") or row.get("city") or ""
        if isinstance(loc, dict):
            loc = loc.get("location_str") or loc.get("city") or loc.get("country") or ""
        remote = bool(row.get("remote")) or ("remote" in str(loc).lower())
        desc = row.get("description") or row.get("full_description") or ""
        out.append(
            RawListing(
                source="Workable ATS",
                company_name=company,
                job_title=str(title),
                job_url=str(job_url),
                location_raw=str(loc) if loc else ("Remote" if remote else None),
                description=_html_to_text(str(desc)) if desc else None,
                posted_at=str(row.get("published_on") or row.get("created_at") or "") or None,
                is_remote_flag_from_board=remote,
                external_id=str(row.get("id") or row.get("shortcode") or job_url),
                canonical_url=str(job_url),
                raw=row,
            )
        )
    return out


async def fetch_themuse(pages: int = 3) -> list[RawListing]:
    """The Muse public jobs API — Software Engineering / Data Science categories."""
    from app.validators.remote_us import us_only as us_only_intake

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
                # Dropped here only while intake is US-only; otherwise every
                # location is kept and the pipeline gate decides.
                if us_only_intake() and loc_names and not any(
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


# --- Jobright ---------------------------------------------------------------
# No public list API: /swan/job/list answers 401 "Cookie not found". Discovery
# uses the paths their robots.txt names explicitly (Allow: /remote-jobs/*,
# Allow: /jobs/*).
#
# Their protection is rate-based, not a ban. A burst of requests earns a
# Cloudflare "Security check" page for a while; requests spaced a couple of
# seconds apart are served normally. So this reads politely and takes a slice
# per run rather than trying to drain the site in one go.
#
# Successive runs advance by themselves: ids already recorded in
# job_source_seen are skipped, so "fetch again tomorrow" reaches new jobs
# instead of re-reading the same first page.
JOBRIGHT_BASE = "https://jobright.ai"
JOBRIGHT_SITEMAP = f"{JOBRIGHT_BASE}/sitemap-remote-jobs.xml"

# Category pages that list jobs. /remote-jobs is the index; the rest are its
# sections. These stayed reachable throughout, unlike the /jobs/<role> pages.
JOBRIGHT_CATEGORIES: tuple[str, ...] = (
    "remote-jobs",
    "remote-jobs/data-ai",
    "remote-jobs/software-engineering",
    "remote-jobs/infrastructure-security",
)

# Which of their sections a picker domain belongs to. The section scopes the
# search; the job title inside it does the narrowing.
JOBRIGHT_DOMAIN_CATEGORY: dict[str, str] = {
    "de": "remote-jobs/data-ai",
    "ai": "remote-jobs/data-ai",
    "ds": "remote-jobs/data-ai",
    "da": "remote-jobs/data-ai",
    "sq": "remote-jobs/data-ai",
    "be": "remote-jobs/software-engineering",
    "fe": "remote-jobs/software-engineering",
    "fs": "remote-jobs/software-engineering",
    "se": "remote-jobs/software-engineering",
    "sf": "remote-jobs/software-engineering",
    "dv": "remote-jobs/infrastructure-security",
    "ce": "remote-jobs/infrastructure-security",
}

# The work model to ask for. The /remote-jobs/ path is only a slug: the filter
# that actually decides is this query param, and the same path serves onsite
# (1) and hybrid (3) when asked. It defaults to 2, but pinning it means a
# change to their default cannot quietly feed onsite jobs to a remote-only app.
JOBRIGHT_REMOTE = 2

# Their seniority filter's whole range. Each value answers with its own thirty,
# and they overlap far less than you would expect: swept across one title,
# 1-6 reached 174 distinct remote jobs where the bare search returned 30.
# 0 and 7+ answer with nothing, so this is the entire ladder.
JOBRIGHT_SENIORITIES: tuple[int, ...] = (1, 2, 3, 4, 5, 6)

# Employment type. Leaving it off is not "everything" — it is full time, and
# only full time: every job an unfiltered page returned came back with
# employmentType "Full-time". Contract and part-time roles are a separate
# pool of the same size, reached only by asking for them.
#
# 2 is contract and part-time. 3 returns the same jobs (29 of 30 identical),
# so it earns nothing, and 4 is internships, which the seniority gate drops
# anyway. Values cannot be combined — `jobTypes=1,2,3` and a repeated param
# both fall back to full time — so each pool costs its own request.
JOBRIGHT_FULL_TIME: int | None = None
JOBRIGHT_CONTRACT = 2
JOBRIGHT_JOB_TYPES: tuple[int | None, ...] = (JOBRIGHT_FULL_TIME, JOBRIGHT_CONTRACT)

# The pages to read for one job title, in the order to read them.
#
# Both employment pools before any depth, then a seniority level at a time
# across both. A run stopped by its time budget then has every title covered
# for both full time and contract, rather than full time explored deeply and
# contract never reached at all.
JOBRIGHT_PASSES: tuple[tuple[int | None, int | None], ...] = tuple(
    [(job_type, None) for job_type in JOBRIGHT_JOB_TYPES]
    + [
        (job_type, seniority)
        for seniority in JOBRIGHT_SENIORITIES
        for job_type in JOBRIGHT_JOB_TYPES
    ]
)

_JOBRIGHT_ID_RE = re.compile(r"/jobs/info/([0-9a-f]{24})")
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# A browser string. Their pages are server-rendered for browsers; the default
# bot agent gets the same content, but this keeps us honest about what we are
# asking for rather than pretending to be a crawler they have not seen.
JOBRIGHT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class JobrightChallenged(RuntimeError):
    """Jobright served a bot challenge instead of the page.

    Their Cloudflare protection engages after a burst of requests and clears
    on its own. The fetcher stops and says so rather than returning an empty
    list, which would read as "no jobs today".
    """


_JOBRIGHT_HEADERS = {
    "User-Agent": JOBRIGHT_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _jobright_html(url: str, timeout: float = 25.0, attempts: int = 2) -> str:
    """Fetch one page, retrying once through a transient challenge.

    Their protection fires intermittently under quick successive requests and
    clears by itself — the same URL that answered with a security check came
    back with 272KB of content a minute later. So one challenged response is
    worth a retry; a second one is an answer rather than a hiccup.
    """
    last = ""
    for attempt in range(1, attempts + 1):
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=_JOBRIGHT_HEADERS
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            last = resp.text
        if "Security check | Jobright" not in last:
            return last
        if attempt < attempts:
            await asyncio.sleep(4.0)
    return last


def _jobright_next_data(html: str) -> dict[str, Any]:
    if "Security check | Jobright" in html or "challenges.cloudflare.com" in html:
        raise JobrightChallenged(
            "Jobright served a Cloudflare bot check — too many requests too "
            "quickly. It clears on its own; fetch again in a few minutes, or "
            "raise the delay between requests."
        )
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))["props"]["pageProps"]
    except Exception:  # noqa: BLE001
        return {}


def _jobright_row(job: dict[str, Any], company: dict[str, Any]) -> RawListing | None:
    """Build a listing from a job page's jobResult / companyResult."""
    title = str(job.get("jobTitle") or job.get("jobNlpTitle") or "").strip()
    employer = str(company.get("companyName") or "").strip()
    job_id = str(job.get("jobId") or "").strip()
    if not title or not employer or not job_id:
        return None

    parts = [
        str(job.get("jobSummary") or "").strip(),
        str(job.get("jdResponsibilitySummary") or "").strip(),
    ]
    # Category pages carry requirements too, and that is the half of a JD the
    # keyword scorer actually reads.
    for key in ("coreResponsibilities", "requirements"):
        value = job.get(key)
        if isinstance(value, list):
            parts += [str(x).strip() for x in value if x]
        elif value:
            parts.append(str(value).strip())
    if company.get("companyDesc"):
        parts.append(str(company["companyDesc"]).strip())
    description = "\n\n".join(p for p in parts if p) or None

    salary = str(job.get("salaryDesc") or "").strip()
    if salary and description:
        description = f"{description}\n\nCompensation: {salary}"

    location = str(job.get("jobLocation") or "").strip() or None
    work_model = str(job.get("workModel") or "").strip()
    if work_model and location and work_model.lower() not in location.lower():
        location = f"{location} ({work_model})"

    return RawListing(
        source="Jobright",
        company_name=employer,
        job_title=title,
        job_url=f"{JOBRIGHT_BASE}/jobs/info/{job_id}",
        location_raw=location,
        description=description,
        posted_at=str(job.get("publishTime") or "") or None,
        is_remote_flag_from_board=(
            str(job.get("isRemote") or "").strip().lower() == "true"
            or work_model.lower() == "remote"
        ),
        external_id=job_id,
        raw={
            "seniority": job.get("jobSeniority"),
            "salary": salary,
            # Full-time / Contract / Part-time. Worth keeping now that the
            # fetcher asks for the contract pool as well: a run that brings
            # both back and labels neither is only half an answer.
            "employment_type": str(job.get("employmentType") or "").strip() or None,
        },
    )


def jobright_ids_from_html(html: str) -> list[str]:
    """Job ids linked from a category page, in page order."""
    return list(dict.fromkeys(_JOBRIGHT_ID_RE.findall(html)))


def _jobright_entries(props: dict[str, Any]) -> list[dict[str, Any]]:
    """The job records a page embeds.

    Category pages carry their whole first page of results in `defaultData`,
    each entry a complete record — title, dates, description, apply link — not
    just a link to fetch. Reading that is the difference between thirteen
    requests and three hundred and forty seven.

    A single job page keeps its one record under `dataSource` instead.
    """
    for key in ("defaultData", "jobList"):
        value = props.get(key)
        if isinstance(value, list) and value:
            return [e for e in value if isinstance(e, dict)]
    single = props.get("dataSource")
    return [single] if isinstance(single, dict) and single else []


def jobright_listings_from_html(html: str) -> list[RawListing]:
    """Every job embedded in a Jobright page, already parsed."""
    rows: list[RawListing] = []
    for entry in _jobright_entries(_jobright_next_data(html)):
        row = _jobright_row(
            entry.get("jobResult") or {}, entry.get("companyResult") or {}
        )
        if row is not None:
            rows.append(row)
    return rows


async def jobright_categories() -> tuple[str, ...]:
    """Every category page their sitemap lists.

    Reading the sitemap rather than a hardcoded few: the constant covered four
    sections and reached 89 jobs, while the sitemap lists thirteen.
    """
    try:
        xml = await _jobright_html(JOBRIGHT_SITEMAP, timeout=20.0)
    except Exception:  # noqa: BLE001
        return JOBRIGHT_CATEGORIES
    found = [
        u.replace(f"{JOBRIGHT_BASE}/", "").rstrip("/")
        for u in re.findall(r"<loc>(.*?)</loc>", xml)
        if "/remote-jobs" in u
    ]
    return tuple(dict.fromkeys(found)) or JOBRIGHT_CATEGORIES


async def jobright_job_urls(
    categories: tuple[str, ...] | None = None,
    request_delay: float = 4.0,
) -> list[str]:
    """Job URLs from the category pages. Cheap: one request per category."""
    if categories is None:
        categories = await jobright_categories()
    ids: list[str] = []
    seen: set[str] = set()
    for index, path in enumerate(categories):
        if index and request_delay:
            await asyncio.sleep(request_delay)
        try:
            html = await _jobright_html(f"{JOBRIGHT_BASE}/{path}")
        except JobrightChallenged:
            raise
        except Exception:  # noqa: BLE001
            continue
        for job_id in jobright_ids_from_html(html):
            if job_id not in seen:
                seen.add(job_id)
                ids.append(job_id)
    return [f"{JOBRIGHT_BASE}/jobs/info/{i}" for i in ids]


async def _jobright_already_seen() -> set[str]:
    """Ids a previous run already ingested, so repeats reach new jobs."""
    try:
        from sqlalchemy import select

        from app.db.models import JobSourceSeen
        from app.db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as db:
            rows = (
                await db.execute(
                    select(JobSourceSeen.external_id).where(
                        JobSourceSeen.source.in_(("Jobright", "jobright"))
                    )
                )
            ).scalars().all()
        return {str(r) for r in rows if r}
    except Exception:  # noqa: BLE001
        return set()


def jobright_search_urls(
    domains: list[str] | None = None,
    roles: list[str] | None = None,
) -> list[str]:
    """The pages to read for a picker selection.

    A category page shows thirty jobs and no more — it has no second page, and
    `page` / `offset` / `pageSize` are all ignored. But `jobTitle` is a real
    server-side filter, and each value answers with its own thirty. So one
    search per job title is what actually reaches the whole list: ten title
    searches under data-ai returned 265 distinct jobs where the plain category
    page gave 30.

    `seniority` is a second real filter, and this is where the volume is. Each
    of its six values answers with its own thirty, largely different jobs —
    sweeping one title across them reached 174 distinct remote jobs where the
    bare search gave 30. Without it the fetcher was reading a sixth of what
    the same URL will hand over.

    `jobTypes` is the third, and leaving it off is not neutral: an unfiltered
    page is full time only. Contract and part-time are a separate pool of much
    the same size — 145 more for the same title — and invisible until asked
    for. So each title is read twice over, once per pool.

    Ticked titles are the selection. The picker opens with every domain
    already on, so "three titles" arrives as twelve domains plus three roles —
    read as "those titles, plus everything in the other eleven" it became 70
    searches and a six minute wait for a request the user meant to be small.
    Domains only contribute their own titles when nothing was ticked anywhere.

    With nothing selected at all this falls back to the plain category pages.
    """
    from app.services.job_domains import (
        JOB_DOMAINS,
        domain_queries,
        role_queries,
        roles_for_domain,
    )

    wanted = [d for d in (domains or []) if d]
    chosen_roles = {r for r in (roles or []) if r}
    if not wanted and not chosen_roles:
        return []

    domain_ids = wanted or [d["id"] for d in JOB_DOMAINS]
    searches: list[tuple[str, str]] = []
    for domain_id in domain_ids:
        category = JOBRIGHT_DOMAIN_CATEGORY.get(domain_id, "remote-jobs")
        picked = [
            r["id"] for r in roles_for_domain(domain_id) if r["id"] in chosen_roles
        ]
        if picked:
            queries: list[str] = []
            for role_id in picked:
                for query in role_queries(role_id):
                    if query not in queries:
                        queries.append(query)
        elif chosen_roles:
            # Titles were ticked, just not in this domain — so it was not asked
            # for. Expanding it here is what turned 3 searches into 70.
            continue
        else:
            queries = domain_queries(domain_id)
        for query in queries:
            pair = (category, query)
            if pair not in searches:
                searches.append(pair)

    # A pass at a time, not a title at a time. Every title is searched at a
    # given depth before any of them goes deeper, so a run cut short by its
    # time budget has covered the whole selection thinly rather than the first
    # two titles exhaustively.
    urls: list[str] = []
    for job_type, seniority in JOBRIGHT_PASSES:
        for category, query in searches:
            url = (
                f"{JOBRIGHT_BASE}/{category}?jobTitle={quote(query)}"
                f"&workModel={JOBRIGHT_REMOTE}"
            )
            if job_type is not None:
                url = f"{url}&jobTypes={job_type}"
            if seniority is not None:
                url = f"{url}&seniority={seniority}"
            urls.append(url)
    return urls


# --- Jobright: the session API ----------------------------------------------
# Their own search, rather than the pages it renders. Two endpoints, and the
# order matters. `count-search-filter-jobs` selects the search: it hands the
# session the filter and answers with the count their UI shows above the
# results. `search` then pages through what that selection matched.
#
# This is worth the cookie it costs. A public category page caps at thirty
# jobs and has no second page, which is why the page fetcher sweeps six
# seniorities across two employment pools — fourteen requests per title to
# reach a few hundred jobs. Here `position` is a real offset, so a title
# drains in one request per twenty jobs, and `daysAgo` asks the server for
# today's postings instead of fetching a week and discarding most of it.
#
# The count is a teaser, not a total: the same filter answered "60+", then
# "40+", then "0" within a few minutes. Paging stops on a short page instead.
JOBRIGHT_API_COUNT = f"{JOBRIGHT_BASE}/swan/recommend/count-search-filter-jobs"
JOBRIGHT_API_SEARCH = f"{JOBRIGHT_BASE}/swan/recommend/search"

# Jobs per search call. Their UI asks for ten; twenty comes back in full and
# halves the requests.
JOBRIGHT_API_PAGE = 20

# Between requests. Their protection is rate-based rather than a ban, and four
# seconds is the spacing the page fetcher already reads at unchallenged.
JOBRIGHT_API_DELAY = 4.0

# A stop for a cursor that never shortens. Paging ends on a short page, which
# every search observed did reach; this is the backstop if one does not.
JOBRIGHT_API_MAX_PAGES = 40

# A typed title rides under a placeholder taxonomy — the id their own UI sends
# for a free-text search. The list cannot be empty: sending `[]` comes back
# with `result` as a bare string where the job list should be.
JOBRIGHT_TAXONOMY_ID = "00-00-00"

# 41001 is no cookie, 41002 a cookie they will not take. Both mean the session
# needs replacing, and neither is worth a retry.
JOBRIGHT_AUTH_ERRORS = frozenset({41001, 41002})


class JobrightNoSession(RuntimeError):
    """Jobright would not accept the session cookie.

    Unlike a bot challenge this does not clear by waiting — it takes a person
    pasting a current cookie into JOBRIGHT_COOKIE. So the run stops and says
    so rather than reporting a search that found nothing.
    """


def jobright_cookie() -> str:
    """The configured session cookie, or "" to read the public pages."""
    from app.config import get_settings

    return (get_settings().jobright_cookie or "").strip()


def _jobright_api_headers(cookie: str) -> dict[str, str]:
    return {
        "User-Agent": JOBRIGHT_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Cookie": cookie,
        "Origin": JOBRIGHT_BASE,
        "Referer": f"{JOBRIGHT_BASE}/jobs/search",
        "x-client-type": "web",
    }


def jobright_api_body(
    title: str,
    *,
    days_ago: int = 1,
    country: str = "US",
    work_model: tuple[int, ...] = (JOBRIGHT_REMOTE,),
    seniority: tuple[int, ...] = (),
    job_types: tuple[int, ...] = (),
    position: int = 0,
    refresh: bool = False,
) -> dict[str, Any]:
    """The filter both endpoints take, as their site sends it.

    Every key is spelled out even where the value is empty or null. Their
    search reads the body as a whole rather than merging it over a stored
    default, so a filter with keys missing is not the filter the browser sends.

    Seniority and employment type are left open. The page fetcher had to sweep
    them because thirty results was all one page would ever give; this one
    pages, so asking for everything at once is both simpler and fewer requests.
    """
    return {
        "value": title,
        "jobTaxonomyList": [{"taxonomyId": JOBRIGHT_TAXONOMY_ID, "title": title}],
        "country": country,
        "jobTypes": list(job_types),
        "seniority": list(seniority),
        "workModel": list(work_model),
        "locations": [],
        "companies": [],
        "daysAgo": days_ago,
        "isH1BOnly": False,
        "companyCategory": None,
        "annualSalaryMinimum": None,
        "roleType": None,
        "companyStages": None,
        "skills": [],
        "excludedCompanies": [],
        "excludedSkills": None,
        "excludeStaffingAgency": False,
        "minYearsOfExperienceRange": None,
        "excludeCompanyCategory": [],
        "excludeSecurityClearance": False,
        "excludeUsCitizen": False,
        "refresh": refresh,
        "position": position,
        "sortCondition": 0,
    }


async def _jobright_api_post(
    client: httpx.AsyncClient, url: str, body: dict[str, Any]
) -> dict[str, Any]:
    """One call, with their error envelope read rather than assumed.

    They answer 200 with `success: false` as readily as they answer 401, so
    the status code alone does not say whether this worked.
    """
    resp = await client.post(url, json=body)
    data: dict[str, Any] = {}
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            data = parsed
    except Exception:  # noqa: BLE001
        data = {}

    if resp.status_code == 401 or data.get("errorCode") in JOBRIGHT_AUTH_ERRORS:
        detail = data.get("errorMsg") or f"HTTP {resp.status_code}"
        raise JobrightNoSession(
            f"Jobright rejected the session cookie ({detail}). Sign in at "
            "jobright.ai, copy the Cookie header from a request in DevTools, "
            "and put it in JOBRIGHT_COOKIE."
        )
    resp.raise_for_status()
    if not data.get("success"):
        raise RuntimeError(
            f"Jobright search failed: {data.get('errorMsg') or 'unknown error'}"
        )

    result = data.get("result")
    return result if isinstance(result, dict) else {}


async def jobright_api_select(
    client: httpx.AsyncClient, body: dict[str, Any]
) -> str:
    """Select the search, before asking it for any jobs.

    This is how their UI opens a search: the count endpoint registers the
    filter against the session and answers with the headline figure. The
    number is the least of it — what the call is really for is putting the
    session into the search that `search` then pages through.
    """
    result = await _jobright_api_post(
        client,
        f"{JOBRIGHT_API_COUNT}?searchType=job_title",
        dict(body, refresh=True, position=0),
    )
    return str(result.get("display") or "").strip()


async def jobright_api_entries(
    client: httpx.AsyncClient,
    title: str,
    *,
    days_ago: int = 1,
    limit: int = 0,
    delay: float = JOBRIGHT_API_DELAY,
    deadline: float = 0.0,
) -> list[dict[str, Any]]:
    """Every job their search returns for one title, paged to the end.

    `position` is an offset into the selected search and the pages do not
    overlap — four pages of a 44-job search returned 44 distinct ids. A page
    shorter than the one asked for is the end of the list.
    """
    body = jobright_api_body(title, days_ago=days_ago)
    await jobright_api_select(client, body)

    out: list[dict[str, Any]] = []
    position = 0
    for _ in range(JOBRIGHT_API_MAX_PAGES):
        if deadline and time.monotonic() > deadline:
            break
        if delay:
            await asyncio.sleep(delay)
        url = (
            f"{JOBRIGHT_API_SEARCH}?searchType=job_title&refresh=false"
            f"&count={JOBRIGHT_API_PAGE}&position={position}&sortCondition=0"
        )
        result = await _jobright_api_post(
            client, url, dict(body, refresh=False, position=position)
        )
        page = [e for e in (result.get("jobList") or []) if isinstance(e, dict)]
        out.extend(page)
        if len(page) < JOBRIGHT_API_PAGE:
            break
        if limit and len(out) >= limit:
            break
        position += JOBRIGHT_API_PAGE
    return out[:limit] if limit else out


def jobright_api_titles(
    domains: list[str] | None = None,
    roles: list[str] | None = None,
) -> list[str]:
    """The titles to search, from the picker's selection.

    The same reading of the picker the page fetcher uses: ticked titles are
    the selection, and a domain contributes its own titles only when nothing
    was ticked anywhere. What this drops is the seniority and employment
    sweep — those existed to work around a page that would not paginate.
    """
    from app.services.job_domains import (
        JOB_DOMAINS,
        domain_queries,
        role_queries,
        roles_for_domain,
    )

    wanted = [d for d in (domains or []) if d]
    chosen_roles = {r for r in (roles or []) if r}
    if not wanted and not chosen_roles:
        return []

    titles: list[str] = []
    for domain_id in wanted or [d["id"] for d in JOB_DOMAINS]:
        picked = [
            r["id"] for r in roles_for_domain(domain_id) if r["id"] in chosen_roles
        ]
        if picked:
            queries = [q for role_id in picked for q in role_queries(role_id)]
        elif chosen_roles:
            continue
        else:
            queries = domain_queries(domain_id)
        for query in queries:
            if query not in titles:
                titles.append(query)
    return titles


async def fetch_jobright_api(
    max_jobs: int = 0,
    roles: list[str] | None = None,
    domains: list[str] | None = None,
    request_delay: float = JOBRIGHT_API_DELAY,
    skip_seen: bool = False,
    time_budget: float = 0.0,
    days_ago: int = 1,
) -> list[RawListing]:
    """Jobright's remote US jobs, read through their own search.

    One search per selected title, each paged to the end. The records come
    back in the same jobResult/companyResult shape their pages embed, so the
    same parser reads both.
    """
    titles = jobright_api_titles(domains, roles)
    if not titles:
        # A search API has to be asked for something — there is no equivalent
        # of falling back to a category page.
        from app.services.job_domains import domain_queries

        titles = list(domain_queries("de"))
    if not titles:
        return []

    cookie = jobright_cookie()
    if not cookie:
        raise JobrightNoSession(
            "No Jobright session — put a signed-in Cookie header in JOBRIGHT_COOKIE."
        )

    seen_ids = await _jobright_already_seen() if skip_seen else set()
    out: list[RawListing] = []
    taken: set[str] = set()
    started = time.monotonic()
    deadline = started + time_budget if time_budget else 0.0

    async with httpx.AsyncClient(
        timeout=40.0, follow_redirects=True, headers=_jobright_api_headers(cookie)
    ) as client:
        for index, title in enumerate(titles):
            if deadline and time.monotonic() > deadline:
                log.info(
                    "jobright api: stopped at %d/%d titles, %d jobs (budget %.0fs)",
                    index,
                    len(titles),
                    len(out),
                    time_budget,
                )
                break
            if index and request_delay:
                await asyncio.sleep(request_delay)
            try:
                entries = await jobright_api_entries(
                    client,
                    title,
                    days_ago=days_ago,
                    delay=request_delay,
                    deadline=deadline,
                )
            except JobrightNoSession:
                # An expired cookie will not come back on the next title, and
                # keeping what earlier titles found beats losing the run.
                if out:
                    log.warning("jobright api: session lost after %d jobs", len(out))
                    return out
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("jobright api: %r failed: %s", title, exc)
                continue

            for entry in entries:
                job = entry.get("jobResult") or {}
                row = _jobright_row(job, entry.get("companyResult") or {})
                if row is None:
                    continue
                job_id = row.external_id or ""
                if not job_id or job_id in taken or job_id in seen_ids:
                    continue
                # The employer's own posting, which their search hands over and
                # the public pages do not. Worth keeping: it is where an
                # application actually gets made.
                apply_link = str(job.get("applyLink") or job.get("originalUrl") or "")
                if apply_link.strip():
                    row.raw["apply_link"] = apply_link.strip()
                taken.add(job_id)
                out.append(row)
                if max_jobs and len(out) >= max_jobs:
                    return out
            log.info(
                "jobright api: %r -> %d jobs (%d total)", title, len(entries), len(out)
            )
    return out


async def fetch_jobright(
    max_jobs: int = 0,
    roles: list[str] | None = None,
    domains: list[str] | None = None,
    request_delay: float = 4.0,
    skip_seen: bool = True,
    time_budget: float = 0.0,
    days_ago: int = 0,
) -> list[RawListing]:
    """Jobright's remote US jobs, by whichever route this install can take.

    With a session cookie configured this is their own search: it pages, so it
    reaches the whole result set, and it filters by date server-side. Without
    one it falls back to reading their public pages, which need no login and
    return a fraction as much.

    The fallback is deliberate rather than a failure mode — a cookie expires,
    and a fetch that stops working the day it does would be worse than one
    that quietly returns less and says which route it took.
    """
    if jobright_cookie():
        log.info(
            "jobright: reading the session API (%d titles)",
            len(jobright_api_titles(domains, roles)),
        )
        return await fetch_jobright_api(
            max_jobs=max_jobs,
            roles=roles,
            domains=domains,
            request_delay=max(request_delay, JOBRIGHT_API_DELAY),
            skip_seen=skip_seen,
            time_budget=time_budget,
            days_ago=days_ago or 1,
        )
    return await fetch_jobright_pages(
        max_jobs=max_jobs,
        roles=roles,
        domains=domains,
        request_delay=request_delay,
        skip_seen=skip_seen,
        time_budget=time_budget,
    )


async def fetch_jobright_pages(
    max_jobs: int = 0,
    roles: list[str] | None = None,
    domains: list[str] | None = None,
    request_delay: float = 4.0,
    skip_seen: bool = True,
    time_budget: float = 0.0,
) -> list[RawListing]:
    """Jobright's remote jobs, read from the pages that already contain them.

    Two things make this cheap enough to run in one click. Category pages
    embed their results in full, so no job needs a second request. And
    `jobTitle` filters server-side, so a selection of titles becomes a
    selection of searches — each answering with its own thirty jobs.

    `max_jobs=0` means no cap: take everything the selection reaches.
    `time_budget` is a wall clock the search stops at and hands back what it
    found — being cancelled from outside would throw all of it away, and every
    selected domain widens the run.
    """
    targets = jobright_search_urls(domains, roles)
    if not targets:
        targets = [f"{JOBRIGHT_BASE}/{path}" for path in await jobright_categories()]
    if not targets:
        return []

    seen_ids = await _jobright_already_seen() if skip_seen else set()
    out: list[RawListing] = []
    taken: set[str] = set()
    started = time.monotonic()
    for index, url in enumerate(targets):
        if time_budget and time.monotonic() - started > time_budget:
            log.info(
                "jobright: stopped at %d/%d searches, %d jobs (budget %.0fs)",
                index, len(targets), len(out), time_budget,
            )
            break
        if index and request_delay:
            await asyncio.sleep(request_delay)
        try:
            html = await _jobright_html(url)
        except JobrightChallenged:
            # Keep what the earlier searches found. Reporting a short run as a
            # clean one is how "no jobs today" gets said about a bot check.
            if out:
                return out
            raise
        except Exception:  # noqa: BLE001
            continue
        for row in jobright_listings_from_html(html):
            job_id = row.external_id or ""
            if not job_id or job_id in taken or job_id in seen_ids:
                continue
            taken.add(job_id)
            out.append(row)
            if max_jobs and len(out) >= max_jobs:
                return out
    return out
