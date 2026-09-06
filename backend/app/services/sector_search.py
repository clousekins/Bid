"""Find jobs at companies in one industry sector.

The operator names a sector they actually work in — "healthcare", "fintech" —
and this searches the public job APIs for it. No API key: company discovery
needs a Google CSE or SerpAPI key and has none configured, so this uses the
feeds that expose a search or industry parameter without credentials, and
keyword-filters the ones that do not.

Results are returned for review rather than inserted. A sector search casts a
wider net than the curated boards, so the operator decides what enters the
pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.adapters.base import RawListing
from app.adapters.boards.fetchers import fetch_json
from app.services.job_domains import classify_domains, domains_storage
from app.validators.dedupe import canonicalize_url, normalize_company, normalize_title
from app.validators.it_domain import check_it_domain
from app.validators.remote_us import check_remote_us
from app.validators.seniority import check_seniority

log = logging.getLogger("poong.sector")

# Two tiers per sector. "strong" words belong to the sector and almost nowhere
# else; "weak" ones are ordinary English that happens to appear in it. Matching
# on weak words alone put a railway maintenance firm in healthcare and GitLab
# in fintech, because every posting mentions health cover, risk and compliance.
SECTOR_HINTS: dict[str, dict[str, tuple[str, ...]]] = {
    "healthcare": {
        "strong": (
            "healthcare", "health care", "telehealth", "ehr", "emr", "hipaa",
            "hospital", "clinical", "clinician", "pharma", "biotech",
            "life sciences", "medicare", "medicaid", "medtech", "diagnostics",
            "health system", "patient care", "care delivery", "payer", "provider network",
            "oncology", "radiology", "genomics", "therapeutics", "medical device",
        ),
        "weak": ("health", "medical", "patient", "care", "wellness", "provider"),
    },
    "fintech": {
        "strong": (
            "fintech", "insurtech", "neobank", "brokerage", "kyc", "aml",
            "underwriting", "capital markets", "wealth management", "payments platform",
            "payment processing", "core banking", "lending", "mortgage",
            "trading platform", "custody", "ledger", "banking", "cryptocurrency",
        ),
        "weak": ("financial", "payments", "insurance", "credit", "risk", "compliance", "trading"),
    },
    "ecommerce": {
        "strong": (
            "ecommerce", "e-commerce", "marketplace", "storefront", "merchant",
            "order fulfillment", "fulfilment", "retail media", "point of sale",
            "shopping cart", "direct to consumer", "dtc",
        ),
        "weak": ("retail", "shopping", "orders", "catalog", "inventory"),
    },
    "education": {
        "strong": (
            "edtech", "curriculum", "learning management", "lms", "k-12", "k12",
            "higher education", "student information", "courseware", "university",
        ),
        "weak": ("education", "learning", "student", "school", "academic", "teaching"),
    },
    "government": {
        "strong": (
            "public sector", "federal agency", "govtech", "fedramp", "gsa",
            "security clearance", "civic tech", "department of defense", "dod",
        ),
        "weak": ("government", "federal", "defense", "municipal", "policy"),
    },
    "media": {
        "strong": (
            "streaming", "broadcast", "publishing", "video on demand", "ott",
            "newsroom", "game studio", "content delivery",
        ),
        "weak": ("media", "entertainment", "gaming", "content", "audience"),
    },
    "energy": {
        "strong": (
            "renewable energy", "solar", "wind farm", "smart grid", "utility grid",
            "oil and gas", "decarbon", "carbon capture", "battery storage",
        ),
        "weak": ("energy", "utility", "grid", "climate", "power"),
    },
    "logistics": {
        "strong": (
            "supply chain", "freight", "last mile", "warehouse management",
            "fleet management", "3pl", "shipping logistics", "route optimization",
        ),
        "weak": ("logistics", "shipping", "transport", "delivery", "warehouse"),
    },
    "real estate": {
        "strong": (
            "proptech", "real estate", "mortgage origination", "property management",
            "mls", "title insurance", "construction tech",
        ),
        "weak": ("property", "housing", "leasing", "construction"),
    },
    "travel": {
        "strong": (
            "hospitality", "airline", "hotel booking", "travel booking",
            "reservations system", "tourism", "gds",
        ),
        "weak": ("travel", "hotel", "booking", "guest"),
    },
    "security": {
        "strong": (
            "cybersecurity", "infosec", "threat detection", "siem", "soc analyst",
            "penetration testing", "vulnerability management", "zero trust",
            "identity and access", "endpoint security",
        ),
        "weak": ("security", "threat", "identity", "encryption"),
    },
    "manufacturing": {
        "strong": (
            "manufacturing", "industrial automation", "plc", "scada", "mes",
            "robotics", "automotive", "aerospace", "factory floor", "industrial iot",
        ),
        "weak": ("industrial", "production", "assembly", "hardware"),
    },
}


@dataclass
class SectorHit:
    company: str
    title: str
    url: str
    location: str | None
    source: str
    description: str | None = None
    posted_at: str | None = None
    matched_on: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        domain_ids = classify_domains(self.title, self.description)
        return {
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "location": self.location,
            "source": self.source,
            "description": self.description,
            "posted_at": self.posted_at,
            "matched_on": self.matched_on,
            "domains": domains_storage(domain_ids),
        }


_BENEFITS_NOISE = re.compile(
    r"[^.\r\n]{0,80}(?:"
    r"health\s*(?:insurance|benefits?|care\s*(?:coverage|plan|benefits?))"
    r"|medical[,/ ]+\s*(?:dental|vision)"
    r"|dental[,/ ]+\s*(?:medical|vision)"
    r"|vision[,/ ]+\s*(?:medical|dental)"
    r"|(?:paid|company|comprehensive|full)\s+(?:medical|health)"
    r"|401\(?k\)?|\bpto\b|paid\s+time\s+off"
    r"|wellness\s+(?:program|stipend|benefits?|reimbursement)"
    r"|mental\s+health\s+(?:support|benefits?|days?|resources?)"
    r"|equal\s+opportunity|benefits\s+package|life\s+insurance"
    r")[^.\r\n]{0,120}",
    re.I,
)


def sector_terms(sector: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(strong, weak) words for a sector.

    An unknown sector still works — the operator's own word becomes the strong
    term, which is the point of letting them type one.
    """
    key = (sector or "").strip().lower()
    if not key:
        return (), ()
    hit = SECTOR_HINTS.get(key)
    if hit is None:
        # Compare without spaces or hyphens: "health care", "health-care" and
        # "healthcare" are the same field and an operator may type any of them.
        squashed = re.sub(r"[\s_-]+", "", key)
        for known, hints in SECTOR_HINTS.items():
            flat = re.sub(r"[\s_-]+", "", known)
            if squashed == flat or squashed in flat or flat in squashed:
                hit = hints
                break
    if hit is None:
        return (key,), ()
    return hit["strong"], hit["weak"]


def _matches(text: str, terms: tuple[str, ...]) -> list[str]:
    body = (text or "").lower()
    return [t for t in terms if re.search(rf"(?<![a-z]){re.escape(t)}", body)]


def _sector_evidence(
    hit: "SectorHit", strong: tuple[str, ...], weak: tuple[str, ...]
) -> list[str]:
    """Words placing this listing in the sector, benefits boilerplate removed.

    Only strong words qualify a listing. Weak ones are reported alongside them
    for context but never carry a match on their own — that is what let a
    railway firm and an account-executive role through.
    """
    where = f"{hit.company} {hit.title}"
    found = _matches(where, strong)
    if found:
        return (found + _matches(where, weak))[:5]
    body = _BENEFITS_NOISE.sub(" ", hit.description or "")
    found = _matches(body, strong)
    if not found:
        return []
    return (found + _matches(body, weak))[:5]


async def _remotive(sector: str) -> list[SectorHit]:
    """Remotive exposes a free-text search."""
    url = f"https://remotive.com/api/remote-jobs?search={sector}&limit=100"
    data = await fetch_json(url)
    rows = data.get("jobs") if isinstance(data, dict) else None
    out = []
    for row in rows or []:
        company, title = row.get("company_name") or "", row.get("title") or ""
        if not company or not title:
            continue
        out.append(
            SectorHit(
                company=company.strip(),
                title=title.strip(),
                url=row.get("url") or "",
                location=row.get("candidate_required_location") or "Remote",
                source="Remotive",
                description=row.get("description") or None,
                posted_at=str(row.get("publication_date") or "") or None,
                matched_on=["remotive:search"],
            )
        )
    return out


async def _jobicy(sector: str) -> list[SectorHit]:
    """Jobicy has an industry parameter."""
    url = f"https://jobicy.com/api/v2/remote-jobs?industry={sector}&count=50"
    data = await fetch_json(url)
    rows = data.get("jobs") if isinstance(data, dict) else None
    out = []
    for row in rows or []:
        company, title = row.get("companyName") or "", row.get("jobTitle") or ""
        if not company or not title:
            continue
        desc = row.get("jobExcerpt") or row.get("jobDescription") or None
        out.append(
            SectorHit(
                company=str(company).strip(),
                title=str(title).strip(),
                url=row.get("url") or "",
                location=row.get("jobGeo") or "Remote",
                source="Jobicy",
                description=str(desc) if desc else None,
                posted_at=str(row.get("pubDate") or "") or None,
                matched_on=["jobicy:industry"],
            )
        )
    return out


async def _themuse(sector: str) -> list[SectorHit]:
    """The Muse filters by industry, and its industry names are capitalised."""
    label = sector.strip().title()
    out = []
    for page in (0, 1):
        try:
            data = await fetch_json(
                "https://www.themuse.com/api/public/jobs"
                f"?industry={label}&page={page}&descending=true"
            )
        except Exception:  # noqa: BLE001
            break
        for row in (data.get("results") if isinstance(data, dict) else None) or []:
            company = ((row.get("company") or {}) or {}).get("name") or ""
            title = row.get("name") or ""
            if not company or not title:
                continue
            locs = [
                str((loc or {}).get("name") or "")
                for loc in (row.get("locations") or [])
            ]
            refs = row.get("refs") or {}
            out.append(
                SectorHit(
                    company=str(company).strip(),
                    title=str(title).strip(),
                    url=refs.get("landing_page") or "",
                    location=", ".join([x for x in locs if x]) or "Remote",
                    source="The Muse",
                    description=row.get("contents") or None,
                    posted_at=str(row.get("publication_date") or "") or None,
                    matched_on=["themuse:industry"],
                )
            )
    return out


async def _keyword_feeds(strong: tuple[str, ...]) -> list[SectorHit]:
    """Feeds with no industry parameter, filtered on the sector's own words."""
    from app.adapters.boards.fetchers import (
        fetch_arbeitnow,
        fetch_himalayas,
        fetch_remoteok,
    )

    async def safe(fn) -> list[RawListing]:
        try:
            return await asyncio.wait_for(fn(), timeout=30)
        except Exception as exc:  # noqa: BLE001
            log.info("sector feed %s failed: %s", getattr(fn, "__name__", fn), exc)
            return []

    listings = await asyncio.gather(
        safe(fetch_remoteok), safe(fetch_arbeitnow), safe(fetch_himalayas)
    )
    out = []
    for group in listings:
        for row in group:
            # Cheap pre-filter; the real test runs in search_sector.
            blob = f"{row.company_name} {row.job_title}"
            if not (_matches(blob, strong) or _matches(row.description or "", strong)):
                continue
            out.append(
                SectorHit(
                    company=row.company_name,
                    title=row.job_title,
                    url=row.job_url or row.canonical_url or "",
                    location=row.location_raw or "Remote",
                    source=row.source,
                    description=row.description,
                    posted_at=row.posted_at,
                )
            )
    return out


async def _catalog_boards(strong: tuple[str, ...], per_board: int = 25) -> list[SectorHit]:
    """Companies already in the ATS catalog whose name says they are in the sector.

    Better evidence than a job board: these are the employer's own Greenhouse,
    Lever or Ashby page, so the listing is first-hand rather than syndicated.
    """
    from sqlalchemy import select

    from app.adapters.boards.fetchers import (
        fetch_ashby_board,
        fetch_greenhouse_board,
        fetch_lever_board,
    )
    from app.db.models import AtsBoard
    from app.db.session import get_session_factory

    fetchers = {
        "greenhouse": fetch_greenhouse_board,
        "lever": fetch_lever_board,
        "ashby": fetch_ashby_board,
    }
    factory = get_session_factory()
    async with factory() as db:
        boards = (
            await db.execute(select(AtsBoard).where(AtsBoard.status == "ok"))
        ).scalars().all()

    picked = [
        b
        for b in boards
        if b.source in fetchers
        and _matches(f"{b.token} {b.company_name or ''}", strong)
    ][:8]
    if not picked:
        return []

    async def one(board):
        try:
            rows = await asyncio.wait_for(fetchers[board.source](board.token), timeout=25)
        except Exception as exc:  # noqa: BLE001
            log.info("sector board %s/%s failed: %s", board.source, board.token, exc)
            return []
        return [
            SectorHit(
                company=r.company_name,
                title=r.job_title,
                url=r.job_url or r.canonical_url or "",
                location=r.location_raw or "Remote",
                source=f"{board.source.title()} ATS",
                description=r.description,
                posted_at=r.posted_at,
            )
            for r in rows[:per_board]
        ]

    groups = await asyncio.gather(*(one(b) for b in picked))
    return [hit for group in groups for hit in group]


async def search_sector(
    sector: str,
    *,
    limit: int = 60,
    it_only: bool = True,
    us_remote_only: bool = True,
) -> dict[str, Any]:
    """Search the public feeds for jobs in one sector."""
    sector = (sector or "").strip()
    if not sector:
        return {"sector": "", "results": [], "counts": {}, "error": "no sector given"}

    strong, weak = sector_terms(sector)

    async def safe(coro, label):
        try:
            return await asyncio.wait_for(coro, timeout=35)
        except Exception as exc:  # noqa: BLE001
            log.info("sector source %s failed: %s", label, exc)
            return []

    groups = await asyncio.gather(
        safe(_remotive(sector), "remotive"),
        safe(_jobicy(sector), "jobicy"),
        safe(_themuse(sector), "themuse"),
        safe(_keyword_feeds(strong), "keyword-feeds"),
        safe(_catalog_boards(strong), "ats-catalog"),
    )

    counts = {
        "fetched": 0, "not_it": 0, "not_remote_us": 0,
        "junior": 0, "duplicate": 0, "off_sector": 0,
    }
    seen_url: set[str] = set()
    seen_role: set[tuple[str, str]] = set()
    results: list[SectorHit] = []

    for group in groups:
        for hit in group:
            counts["fetched"] += 1
            canon = canonicalize_url(hit.url)
            role = (normalize_company(hit.company), normalize_title(hit.title))
            if (canon and canon in seen_url) or role in seen_role:
                counts["duplicate"] += 1
                continue
            if it_only:
                if not check_seniority(hit.title).ok:
                    counts["junior"] += 1
                    continue
                if not check_it_domain(hit.title, hit.description).ok:
                    counts["not_it"] += 1
                    continue
            if us_remote_only and not check_remote_us(hit.location, hit.description).ok:
                counts["not_remote_us"] += 1
                continue
            # A feed saying "healthcare" is a claim, not evidence: Remotive's
            # search returned staffing agencies whose only tie to the sector
            # was the benefits paragraph. Require the words ourselves.
            evidence = _sector_evidence(hit, strong, weak)
            if not evidence:
                counts["off_sector"] += 1
                continue
            hit.matched_on = evidence
            if canon:
                seen_url.add(canon)
            seen_role.add(role)
            results.append(hit)
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break

    # Strongest sector evidence first.
    results.sort(key=lambda h: (-len(h.matched_on), h.company.lower()))
    return {
        "sector": sector,
        "terms": list(strong)[:12],
        "counts": counts,
        "returned": len(results),
        "results": [h.as_dict() for h in results],
    }
