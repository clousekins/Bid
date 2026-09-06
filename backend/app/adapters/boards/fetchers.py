from __future__ import annotations

import asyncio
import json
import html
import re
from html.parser import HTMLParser

import httpx

from app.adapters.base import RawListing

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
    if "remote" not in blob and "us only" not in blob and "united states" not in blob:
        # Keep US onsite? No — we want remote US. Allow if location says USA/US remote.
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
    core = job.get("coreResponsibilities")
    if isinstance(core, list):
        parts += [str(x).strip() for x in core if x]
    elif core:
        parts.append(str(core).strip())
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
        raw={"seniority": job.get("jobSeniority"), "salary": salary},
    )


def jobright_ids_from_html(html: str) -> list[str]:
    """Job ids linked from a category page, in page order."""
    return list(dict.fromkeys(_JOBRIGHT_ID_RE.findall(html)))


async def jobright_job_urls(
    categories: tuple[str, ...] = JOBRIGHT_CATEGORIES,
    request_delay: float = 5.0,
) -> list[str]:
    """Job URLs from the category pages. Cheap: one request per category."""
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


async def fetch_jobright(
    max_jobs: int = 20,
    roles: list[str] | None = None,
    request_delay: float = 5.0,
    skip_seen: bool = True,
) -> list[RawListing]:
    """A politely paced slice of Jobright's remote jobs.

    One request at a time, spaced apart, capped per run. Run it again to get
    the next slice — ids already ingested are skipped, so repeated runs walk
    forward through the site instead of re-reading page one.
    """
    del roles  # titles are filtered downstream, once the JD is known
    urls = await jobright_job_urls(request_delay=request_delay)
    if not urls:
        return []

    if skip_seen:
        seen = await _jobright_already_seen()
        fresh = [u for u in urls if u.rsplit("/", 1)[-1] not in seen]
        # If everything on these pages is already ingested there is nothing new
        # to take; returning the old ones would just be re-work for the gates.
        urls = fresh or []

    out: list[RawListing] = []
    for index, url in enumerate(urls[: max(1, max_jobs)]):
        if index and request_delay:
            await asyncio.sleep(request_delay)
        try:
            html = await _jobright_html(url)
        except JobrightChallenged:
            # Keep what we already have; the caller reports the interruption.
            if out:
                return out
            raise
        except Exception:  # noqa: BLE001
            continue
        props = _jobright_next_data(html)
        source = props.get("dataSource") or {}
        row = _jobright_row(
            source.get("jobResult") or {}, source.get("companyResult") or {}
        )
        if row is not None:
            out.append(row)
    return out
