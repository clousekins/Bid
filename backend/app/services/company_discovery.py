"""Discover real company ATS boards via OpenAI web search, SerpAPI/CSE, or ATS slug probing."""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AtsBoard, Job
from app.services.ats_catalog import add_boards, catalog_stats, seed_ats_catalog
from app.validators.dedupe import normalize_company

log = logging.getLogger("poong.company_discovery")

ProgressCb = Callable[[dict[str, Any]], Awaitable[None]] | None

GREENHOUSE_RE = re.compile(
    r"(?:boards(?:-api)?\.greenhouse\.io)/(?:embed/)?(?:job_board/)?(?:js/)?([a-z0-9][a-z0-9\-_]*)",
    re.I,
)
GREENHOUSE_PATH_RE = re.compile(
    r"boards\.greenhouse\.io/([a-z0-9][a-z0-9\-_]*)(?:/|$)",
    re.I,
)
LEVER_RE = re.compile(
    r"(?:jobs\.lever\.co|api\.lever\.co/v0/postings)/([a-z0-9][a-z0-9\-_]*)(?:/|$|\?)",
    re.I,
)
ASHBY_RE = re.compile(
    r"(?:jobs\.ashbyhq\.com|api\.ashbyhq\.com/posting-api/job-board)/([a-z0-9][a-z0-9\-_]*)(?:/|$|\?)",
    re.I,
)
WORKABLE_RE = re.compile(
    r"(?:apply\.workable\.com|jobs\.workable\.com)/([a-z0-9][a-z0-9\-_]*)(?:/|$|\?)",
    re.I,
)
URL_IN_TEXT_RE = re.compile(r"https?://[^\s\]\)\"'<>]+", re.I)

SKIP_TOKENS = {
    "www",
    "http",
    "https",
    "jobs",
    "job",
    "embed",
    "v1",
    "v0",
    "boards",
    "api",
    "postings",
    "posting-api",
    "job-board",
    "js",
    "accounts",
}

ATS_DOMAINS = (
    "boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "api.ashbyhq.com",
    "apply.workable.com",
)

USER_AGENT = "RemoteApplyBot/1.0 (+local; company-discovery)"


def extract_ats_from_url(url: str) -> tuple[str, str] | None:
    """Return (source, token) if URL points at a known public ATS board."""
    text = (url or "").strip()
    if not text:
        return None
    try:
        host = (urlparse(text).hostname or "").lower()
    except Exception:  # noqa: BLE001
        host = ""

    for pattern, source in (
        (GREENHOUSE_PATH_RE, "greenhouse"),
        (GREENHOUSE_RE, "greenhouse"),
        (LEVER_RE, "lever"),
        (ASHBY_RE, "ashby"),
        (WORKABLE_RE, "workable"),
    ):
        m = pattern.search(text)
        if not m:
            continue
        token = m.group(1).strip().lower().rstrip("/")
        if not token or token in SKIP_TOKENS or len(token) < 2:
            continue
        if source == "greenhouse" and "greenhouse" not in host and "greenhouse" not in text.lower():
            continue
        return source, token
    return None


def company_name_to_slug(name: str) -> str:
    text = normalize_company(name) or (name or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:64]


def load_company_discovery_config() -> dict[str, Any]:
    path = get_settings().config_dir / "company_discovery.yml"
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            data = {}
    queries = [str(q).strip() for q in (data.get("search_queries") or []) if str(q).strip()]
    if not queries:
        queries = [
            'site:boards.greenhouse.io "software engineer" remote US',
            'site:jobs.lever.co "software engineer" remote US',
            'site:jobs.ashbyhq.com "software engineer" remote US',
        ]
    probe_slugs = [
        company_name_to_slug(str(x))
        for x in (data.get("probe_slugs") or [])
        if str(x).strip()
    ]
    return {
        "max_boards_per_run": max(10, int(data.get("max_boards_per_run") or 500)),
        "max_queries_per_run": max(1, int(data.get("max_queries_per_run") or 24)),
        "results_per_page": max(1, min(10, int(data.get("results_per_page") or 10))),
        "max_pages_per_query": max(1, min(10, int(data.get("max_pages_per_query") or 5))),
        "openai_queries_per_run": max(1, int(data.get("openai_queries_per_run") or 6)),
        "openai_fail_fast": max(1, int(data.get("openai_fail_fast") or 2)),
        "probe_concurrency": max(2, min(20, int(data.get("probe_concurrency") or 12))),
        "probe_limit": max(20, int(data.get("probe_limit") or 400)),
        "search_queries": queries,
        "probe_slugs": [s for s in probe_slugs if s],
    }


def search_provider_status() -> dict[str, Any]:
    s = get_settings()
    has_cse = bool(s.google_cse_api_key and s.google_cse_cx)
    has_serp = bool(s.serpapi_key)
    has_openai = bool(s.openai_api_key)
    # Prefer dedicated search APIs; OpenAI web search next; slug probe always available.
    # When OpenAI is unreachable, Discover companies still works via ATS probing.
    if has_cse:
        provider = "google_cse"
    elif has_serp:
        provider = "serpapi"
    elif has_openai:
        provider = "openai_web_search"
    else:
        provider = "ats_probe"
    return {
        "google_cse": has_cse,
        "serpapi": has_serp,
        "openai_web_search": has_openai,
        "ats_probe": True,
        "ready": True,
        "provider": provider,
    }


async def _search_google_cse(query: str, *, start: int, num: int) -> list[dict[str, str]]:
    s = get_settings()
    params = {
        "key": s.google_cse_api_key,
        "cx": s.google_cse_cx,
        "q": query,
        "num": num,
        "start": start,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get("https://www.googleapis.com/customsearch/v1", params=params)
        r.raise_for_status()
        data = r.json()
    out: list[dict[str, str]] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if not link:
            continue
        out.append(
            {
                "url": link,
                "title": str(item.get("title") or ""),
                "snippet": str(item.get("snippet") or ""),
            }
        )
    return out


async def _search_serpapi(query: str, *, start: int, num: int) -> list[dict[str, str]]:
    s = get_settings()
    params = {
        "engine": "google",
        "q": query,
        "api_key": s.serpapi_key,
        "num": num,
        "start": max(0, start - 1),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get("https://serpapi.com/search.json", params=params)
        r.raise_for_status()
        data = r.json()
    out: list[dict[str, str]] = []
    for item in data.get("organic_results") or []:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if not link:
            continue
        out.append(
            {
                "url": link,
                "title": str(item.get("title") or ""),
                "snippet": str(item.get("snippet") or ""),
            }
        )
    return out


def _walk_collect_urls(obj: Any, urls: list[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {"url", "link", "href"} and isinstance(value, str) and value.startswith("http"):
                urls.append(value)
            else:
                _walk_collect_urls(value, urls)
    elif isinstance(obj, list):
        for item in obj:
            _walk_collect_urls(item, urls)
    elif isinstance(obj, str) and obj.startswith("http"):
        urls.append(obj)


def _response_to_dict(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        try:
            return response.model_dump()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(response, dict):
        return response
    return {}


async def _search_openai_web(query: str, *, num: int = 10) -> list[dict[str, str]]:
    s = get_settings()
    client = AsyncOpenAI(api_key=s.openai_api_key, timeout=25.0, max_retries=0)
    prompt = (
        "Search the public web for US remote software engineering career pages.\n"
        f"Search query to run: {query}\n\n"
        "Return only real Greenhouse, Lever, Ashby, or Workable job/career URLs you found. "
        f"Prefer up to {num} distinct company board URLs. "
        "Do not invent URLs. List each URL on its own line."
    )
    tools_variants: list[list[dict[str, Any]]] = [
        [
            {
                "type": "web_search",
                "filters": {"allowed_domains": list(ATS_DOMAINS)},
            }
        ],
        [{"type": "web_search"}],
    ]
    last_err: Exception | None = None
    response = None
    for tools in tools_variants:
        try:
            kwargs: dict[str, Any] = {
                "model": s.openai_model,
                "tools": tools,
                "input": prompt,
            }
            if tools[0]["type"] == "web_search":
                kwargs["include"] = ["web_search_call.action.sources"]
            response = await client.responses.create(**kwargs)
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning("openai web_search variant failed: %s", exc)
            # Connection errors will fail every variant — stop early
            if "Connection" in type(exc).__name__ or "Connection" in str(exc):
                break
            continue
    if response is None:
        raise RuntimeError(f"OpenAI web search failed: {last_err}")

    data = _response_to_dict(response)
    raw_urls: list[str] = []
    _walk_collect_urls(data, raw_urls)
    text = ""
    if hasattr(response, "output_text") and response.output_text:
        text = str(response.output_text)
    elif isinstance(data.get("output_text"), str):
        text = data["output_text"]
    raw_urls.extend(URL_IN_TEXT_RE.findall(text or ""))

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for url in raw_urls:
        cleaned = url.rstrip(").,];\"'")
        if cleaned in seen:
            continue
        seen.add(cleaned)
        if not extract_ats_from_url(cleaned):
            continue
        out.append({"url": cleaned, "title": "", "snippet": text[:240]})
        if len(out) >= num:
            break
    return out


async def search_web(query: str, *, start: int = 1, num: int = 10) -> list[dict[str, str]]:
    status = search_provider_status()
    if status["provider"] == "google_cse":
        return await _search_google_cse(query, start=start, num=num)
    if status["provider"] == "serpapi":
        return await _search_serpapi(query, start=start, num=num)
    if status["provider"] == "openai_web_search":
        return await _search_openai_web(query, num=num)
    raise RuntimeError("search_web not available for provider " + str(status["provider"]))


def _company_hint(title: str, snippet: str, token: str) -> str:
    blob = f"{title} {snippet}".strip()
    if blob:
        first = re.split(r"[|\-–:·•]", blob, maxsplit=1)[0].strip()
        if 2 <= len(first) <= 80 and "http" not in first.lower():
            return first[:255]
    return token.replace("-", " ").title()


async def _probe_one(
    client: httpx.AsyncClient, source: str, token: str
) -> tuple[str, str, str] | None:
    """Return (source, token, company_name) only when the board has ≥1 live job.

    Empty boards are ambiguous on Lever/Ashby (miss vs zero openings). Industry
    aggregators treat ``count > 0`` as the confirmation signal.
    """
    token = token.strip().lower()
    if not token or token in SKIP_TOKENS:
        return None
    try:
        if source == "greenhouse":
            r = await client.get(
                f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
            )
            if r.status_code != 200:
                return None
            data = r.json() if r.content else {}
            jobs = (data or {}).get("jobs") if isinstance(data, dict) else None
            if not isinstance(jobs, list) or not jobs:
                return None
            name = token.replace("-", " ").title()
            # Prefer board name endpoint when cheap; fall back to token
            try:
                meta = await client.get(
                    f"https://boards-api.greenhouse.io/v1/boards/{token}"
                )
                if meta.status_code == 200:
                    body = meta.json() if meta.content else {}
                    name = str((body or {}).get("name") or name).strip() or name
            except Exception:  # noqa: BLE001
                pass
            return "greenhouse", token, name[:255]
        if source == "lever":
            # 404 = unknown slug; 200 + non-empty list = confirmed board
            r = await client.get(f"https://api.lever.co/v0/postings/{token}?mode=json")
            if r.status_code != 200:
                return None
            rows = r.json()
            if not isinstance(rows, list) or not rows:
                return None
            return "lever", token, token.replace("-", " ").title()
        if source == "ashby":
            r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
            if r.status_code != 200:
                return None
            data = r.json() if r.content else {}
            if not isinstance(data, dict) or "jobs" not in data:
                return None
            jobs = data.get("jobs") or []
            if not isinstance(jobs, list) or not jobs:
                return None
            name = str(
                data.get("organizationName") or data.get("companyName") or token
            ).strip() or token
            return "ashby", token, name.replace("-", " ").title()[:255]
        if source == "workable":
            r = await client.get(
                f"https://apply.workable.com/api/v1/widget/accounts/{token}"
            )
            if r.status_code != 200:
                r = await client.get(
                    f"https://apply.workable.com/api/v3/accounts/{token}/jobs"
                )
            if r.status_code != 200:
                return None
            data = r.json() if r.content else {}
            jobs: list = []
            name = token.replace("-", " ").title()
            if isinstance(data, dict):
                name = str(data.get("name") or data.get("company") or name).strip() or name
                jobs = data.get("jobs") or data.get("results") or []
            elif isinstance(data, list):
                jobs = data
            if not jobs:
                return None
            return "workable", token, name[:255]
    except Exception as exc:  # noqa: BLE001
        log.debug("probe %s/%s failed: %s", source, token, exc)
    return None


async def collect_probe_candidates(session: AsyncSession, cfg: dict[str, Any]) -> list[str]:
    """Build slug candidates from config, existing jobs, and catalog gaps."""
    existing = {
        t
        for (t,) in (
            await session.execute(select(AtsBoard.token))
        ).all()
    }
    slugs: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        slug = company_name_to_slug(raw)
        if not slug or slug in seen or slug in existing or slug in SKIP_TOKENS:
            return
        seen.add(slug)
        slugs.append(slug)

    for s in cfg.get("probe_slugs") or []:
        _add(s)

    # Recent job companies → candidate slugs (best practice: derive from known employers)
    rows = (
        await session.execute(
            select(Job.company).where(Job.company.is_not(None)).order_by(Job.id.desc()).limit(400)
        )
    ).all()
    for (company,) in rows:
        _add(str(company))

    # Also harvest ATS tokens already present in job URLs
    urls = (
        await session.execute(
            select(Job.url).where(Job.url.is_not(None)).order_by(Job.id.desc()).limit(500)
        )
    ).all()
    for (url,) in urls:
        pair = extract_ats_from_url(str(url or ""))
        if pair:
            _add(pair[1])

    return slugs[: cfg["probe_limit"]]


async def discover_by_ats_probe(
    session: AsyncSession,
    *,
    cfg: dict[str, Any],
    limit: int,
    on_progress: ProgressCb = None,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    """Probe Greenhouse → Lever → Ashby → Workable for candidate company slugs.

    Industry practice: try Greenhouse first (largest share), then Lever/Ashby/Workable.
    Per-source timeouts + early stop once ``limit`` boards are found.
    """
    candidates = await collect_probe_candidates(session, cfg)
    found: list[tuple[str, str, str]] = []
    errors: list[str] = []
    probed = 0
    lock = asyncio.Lock()
    stop = asyncio.Event()
    sem = asyncio.Semaphore(cfg["probe_concurrency"])
    # Greenhouse first ≈ 80% of tech boards (aggregator best practice)
    sources = ("greenhouse", "lever", "ashby", "workable")
    timeout = httpx.Timeout(5.0, connect=3.0)
    per_source_s = 6.0

    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:

        async def _emit(force: bool = False) -> None:
            if not on_progress:
                return
            if not force and probed % 10 != 0:
                return
            await on_progress(
                {
                    "provider": "ats_probe",
                    "mode": "ats_probe",
                    "probed": probed,
                    "candidates": len(candidates),
                    "seen": len(found),
                    "added": 0,
                    "done": probed,
                    "total": len(candidates),
                }
            )

        async def one(slug: str) -> None:
            nonlocal probed
            if stop.is_set():
                return
            if cancel_check and await cancel_check():
                stop.set()
                return
            async with sem:
                if stop.is_set():
                    return
                hit = None
                for source in sources:
                    if stop.is_set():
                        break
                    try:
                        async with asyncio.timeout(per_source_s):
                            hit = await _probe_one(client, source, slug)
                    except TimeoutError:
                        errors.append(f"timeout:{source}:{slug}")
                        continue
                    if hit:
                        break
                async with lock:
                    if hit and len(found) < limit:
                        found.append(hit)
                        if len(found) >= limit:
                            stop.set()
                    probed += 1
                await _emit()

        await asyncio.gather(*(one(slug) for slug in candidates), return_exceptions=True)

    if on_progress:
        await on_progress(
            {
                "provider": "ats_probe",
                "mode": "ats_probe",
                "probed": probed,
                "candidates": len(candidates),
                "seen": len(found),
                "done": probed,
                "total": len(candidates),
            }
        )

    uniq: list[tuple[str, str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for source, token, name in found:
        key = (source, token)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        uniq.append((source, token, name))

    added = await add_boards(session, uniq)
    await session.commit()
    stats = await catalog_stats(session)
    return {
        "ok": True,
        "provider": "ats_probe",
        "mode": "ats_probe",
        "queries_run": 0,
        "pages_ok": 0,
        "probed": probed,
        "candidates": len(candidates),
        "seen": len(uniq),
        "added": added,
        "errors": errors[:12],
        "boards": [
            {"source": s, "token": t, "company_name": name}
            for s, t, name in uniq[:80]
        ],
        "ats": stats,
        "message": (
            f"Probed {probed}/{len(candidates)} company slugs; "
            f"added {added} new ATS boards."
        ),
    }


async def _openai_reachable() -> bool:
    s = get_settings()
    if not s.openai_api_key:
        return False
    try:
        client = AsyncOpenAI(api_key=s.openai_api_key, timeout=5.0, max_retries=0)
        async with asyncio.timeout(6.0):
            await client.models.list()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("OpenAI unreachable for company discovery: %s", exc)
        return False


async def discover_companies(
    session: AsyncSession,
    *,
    max_boards: int | None = None,
    on_progress: ProgressCb = None,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    """Discover ATS boards via public slug probing (primary) or web search when healthy."""
    cfg = load_company_discovery_config()
    status = search_provider_status()
    limit = max_boards or cfg["max_boards_per_run"]
    if on_progress:
        await on_progress(
            {
                "phase": "seed",
                "provider": status.get("provider"),
                "done": 0,
                "total": 1,
                "added": 0,
                "seen": 0,
            }
        )
    # Lightweight seed only — demote-404 runs at app startup, not mid-job.
    async with asyncio.timeout(20):
        await seed_ats_catalog(session, demote_missing=False)
        await session.commit()

    provider = status["provider"]

    # OpenAI web_search is flaky here (hangs / connection errors). Prefer ATS probe
    # unless Google CSE or SerpAPI is configured.
    if provider in {"openai_web_search", "ats_probe", None} or not status.get("ready"):
        if on_progress:
            await on_progress(
                {
                    "phase": "ats_probe",
                    "provider": "ats_probe",
                    "fallback_from": provider if provider != "ats_probe" else None,
                }
            )
        probe = await discover_by_ats_probe(
            session,
            cfg=cfg,
            limit=limit,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        if provider and provider != "ats_probe":
            probe["fallback_from"] = provider
            probe["message"] = (
                f"Used ATS slug probing (skipped {provider}). "
                + str(probe.get("message") or "")
            )
        return probe

    # Google CSE / SerpAPI path
    if provider in {"google_cse", "serpapi"}:
        queries = (
            cfg["search_queries"][: cfg["openai_queries_per_run"]]
            if provider == "openai_web_search"
            else cfg["search_queries"][: cfg["max_queries_per_run"]]
        )
        max_pages = 1 if provider == "openai_web_search" else cfg["max_pages_per_query"]
        found: list[tuple[str, str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()
        errors: list[str] = []
        pages_ok = 0
        consecutive_fails = 0
        abort_search = False

        for qi, query in enumerate(queries):
            if cancel_check and await cancel_check():
                break
            if abort_search or len(found) >= limit:
                break
            for page in range(max_pages):
                if len(found) >= limit:
                    break
                start = 1 + page * cfg["results_per_page"]
                try:
                    # Hard cap so a hung OpenAI/web_search call cannot stall the whole job
                    search_timeout = 35.0 if provider == "openai_web_search" else 45.0
                    async with asyncio.timeout(search_timeout):
                        results = await search_web(
                            query, start=start, num=cfg["results_per_page"]
                        )
                    pages_ok += 1
                    consecutive_fails = 0
                except Exception as exc:  # noqa: BLE001
                    consecutive_fails += 1
                    errors.append(f"{query[:60]}: {exc}"[:240])
                    log.warning("company search failed q=%s: %s", query[:80], exc)
                    if (
                        consecutive_fails >= cfg["openai_fail_fast"]
                        or "Connection" in type(exc).__name__
                        or "Connection" in str(exc)
                        or "Timeout" in type(exc).__name__
                        or "timeout" in str(exc).lower()
                    ):
                        abort_search = True
                    break
                if not results:
                    break
                for item in results:
                    pair = extract_ats_from_url(item["url"])
                    if not pair:
                        continue
                    source, token = pair
                    key = (source, token)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    hint = _company_hint(
                        item.get("title") or "", item.get("snippet") or "", token
                    )
                    found.append((source, token, hint))
                    if len(found) >= limit:
                        break
            if on_progress:
                await on_progress(
                    {
                        "provider": provider,
                        "queries_run": qi + 1,
                        "pages_ok": pages_ok,
                        "seen": len(found),
                        "done": qi + 1,
                        "total": len(queries),
                        "errors": errors[-3:],
                    }
                )
            if abort_search:
                log.warning(
                    "web search abort after %s consecutive errors; falling back to ATS probe",
                    consecutive_fails,
                )
                break

        if found:
            added = await add_boards(session, found)
            await session.commit()
            stats = await catalog_stats(session)
            return {
                "ok": True,
                "provider": provider,
                "queries_run": len(queries),
                "pages_ok": pages_ok,
                "seen": len(found),
                "added": added,
                "errors": errors[:12],
                "boards": [
                    {"source": s, "token": t, "company_name": name}
                    for s, t, name in found[:80]
                ],
                "ats": stats,
            }

        # Fall through to probe when search found nothing / failed
        probe = await discover_by_ats_probe(
            session,
            cfg=cfg,
            limit=limit,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        probe["fallback_from"] = provider
        probe["search_errors"] = errors[:12]
        return probe

    return await discover_by_ats_probe(
        session,
        cfg=cfg,
        limit=limit,
        on_progress=on_progress,
        cancel_check=cancel_check,
    )
