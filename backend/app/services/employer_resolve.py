from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.config import get_settings
from app.validators.dedupe import normalize_company
from app.services.openai_compat import chat_completion


# Job boards / marketplaces / aggregators — not the hiring employer
JOB_BOARD_NAMES = {
    "jobgether",
    "dice",
    "indeed",
    "ziprecruiter",
    "linkedin",
    "linkedin jobs",
    "glassdoor",
    "wellfound",
    "angel list",
    "angellist",
    "otta",
    "builtin",
    "built in",
    "remoteok",
    "remote ok",
    "weworkremotely",
    "we work remotely",
    "flexjobs",
    "simplyhired",
    "monster",
    "careerbuilder",
    "hired",
    "talent.com",
    "jooble",
    "adzuna",
    "snagajob",
    "clearancejobs",
    "ladders",
    "the ladders",
    "himalayas",
    "remotive",
    "working nomads",
    "nodesk",
    "jobspresso",
    "remote.co",
    "justremote",
    "eu remote jobs",
    "hacker news",
    "hn who is hiring",
}

# Staffing / recruiting firms — real client is usually in the JD
STAFFING_HINTS = {
    "staffing",
    "recruiting",
    "recruitment",
    "talent solutions",
    "talent partners",
    "consulting services",
    "workforce",
    "manpower",
    "robert half",
    "kforce",
    "teksystems",
    "insight global",
    "randstad",
    "adecco",
    "hays",
    "aerotek",
    "apex systems",
    "modis",
    "experis",
    "kelly services",
    "actalent",
    "motion recruitment",
    "cybercoders",
    "jobot",
}

_EXTRACT_PATTERNS = [
    re.compile(
        r"(?im)^(?:hiring\s+(?:company|organization|client)|"
        r"company\s*name|client\s*(?:company|name)?|employer|"
        r"about\s+the\s+(?:company|client)|our\s+client)\s*[:\-–]\s*(.+)$"
    ),
    re.compile(
        r"(?i)\b(?:client|employer|company)\s*[:\-–]\s*([A-Z][A-Za-z0-9&.,'+\- ]{1,70})"
    ),
    re.compile(
        r"(?i)\bjoin\s+([A-Z][A-Za-z0-9&.,'+\- ]{1,50}?)\s+(?:as|to)\b"
    ),
    re.compile(
        r"(?i)\bat\s+([A-Z][A-Za-z0-9&.,'+\- ]{1,50}?)\s+(?:is\s+hiring|we(?:'re| are)\s+looking)\b"
    ),
]

_BAD_CLIENT = re.compile(
    r"(?i)^(the|a|an|our|this|their|company|client|employer|team|role|position|"
    r"job|remote|united states|usa|us|you|we|i)$"
)


@dataclass
class EmployerResolve:
    company: str
    source_name: str | None = None  # job board / staffing firm kept for reference
    changed: bool = False
    method: str = "unchanged"
    confidence: str = "high"


def _norm_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def is_job_board_or_aggregator(name: str) -> bool:
    key = _norm_key(name)
    if not key:
        return False
    if key in JOB_BOARD_NAMES or key.replace(" ", "") in {x.replace(" ", "") for x in JOB_BOARD_NAMES}:
        return True
    # partial: "Jobgether Inc"
    for board in JOB_BOARD_NAMES:
        if board in key or key in board:
            return True
    return False


def is_staffing_firm(name: str) -> bool:
    key = _norm_key(name)
    if not key:
        return False
    return any(h in key for h in STAFFING_HINTS)


def needs_client_resolution(company: str, url: str = "") -> bool:
    if is_job_board_or_aggregator(company) or is_staffing_firm(company):
        return True
    u = (url or "").lower()
    boards_in_url = (
        "jobgether",
        "dice.com",
        "indeed.com",
        "ziprecruiter",
        "linkedin.com/jobs",
        "glassdoor",
        "wellfound",
        "otta.com",
        "builtin.com",
        "remoteok",
        "weworkremotely",
    )
    return any(b in u for b in boards_in_url)


def _clean_candidate(raw: str) -> str | None:
    s = re.sub(r"\s+", " ", (raw or "").strip(" \t\r\n\"'`*|"))
    s = re.sub(r"\s*[\(\[].*?[\)\]]\s*$", "", s).strip()
    s = re.split(r"[|•·\n]| - ", s)[0].strip()
    if len(s) < 2 or len(s) > 80:
        return None
    if _BAD_CLIENT.match(s):
        return None
    if is_job_board_or_aggregator(s):
        return None
    # Reject sentences
    if s.count(" ") > 8 or s.endswith("."):
        return None
    return s


def extract_employer_heuristic(jd: str, *, listing_company: str = "") -> str | None:
    text = jd or ""
    if not text:
        return None
    # Prefer early JD (company usually near top)
    head = text[:6000]
    for pat in _EXTRACT_PATTERNS:
        for m in pat.finditer(head):
            cand = _clean_candidate(m.group(1))
            if cand and _norm_key(cand) != _norm_key(listing_company):
                return cand
    # "About Acme Corp" heading
    m = re.search(
        r"(?im)^about\s+([A-Z][A-Za-z0-9&.,'+\- ]{1,60})\s*$",
        head,
    )
    if m:
        cand = _clean_candidate(m.group(1))
        if cand and _norm_key(cand) != _norm_key(listing_company):
            return cand
    return None


async def extract_employer_llm(
    *,
    listing_company: str,
    title: str,
    url: str,
    jd: str,
) -> str | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    payload = {
        "listing_company": listing_company,
        "title": title,
        "url": url,
        "jd_excerpt": (jd or "")[:9000],
        "instruction": (
            "Extract the real hiring employer / client company name from this job posting. "
            "The listing_company is often a job board (e.g. Jobgether) or staffing firm — "
            "do NOT return that. Return JSON only: "
            '{"company":"Exact Name","confidence":"high|medium|low"}. '
            "If unknown, company=null."
        ),
    }
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await chat_completion(
            client,
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You identify the true hiring company for job posts on aggregators. "
                        "Never invent a company. Prefer the client/employer named in the JD. "
                        "Reply with strict JSON only."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.M)
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        company = data.get("company")
        if not company or not str(company).strip():
            return None
        conf = str(data.get("confidence") or "").lower()
        if conf == "low":
            return None
        return _clean_candidate(str(company))
    except Exception:  # noqa: BLE001
        return None


async def resolve_employer(
    *,
    listing_company: str,
    title: str,
    url: str,
    jd: str,
    force: bool = False,
) -> EmployerResolve:
    """Return the best company name for folders/dedupe/applied tracking."""
    listing = (listing_company or "").strip() or "Unknown"
    if not force and not needs_client_resolution(listing, url):
        # Still try a light heuristic if JD clearly says "Client: X"
        hit = extract_employer_heuristic(jd, listing_company=listing)
        if hit and _norm_key(hit) != _norm_key(listing):
            return EmployerResolve(
                company=hit,
                source_name=listing if is_job_board_or_aggregator(listing) or is_staffing_firm(listing) else None,
                changed=True,
                method="heuristic",
                confidence="medium",
            )
        return EmployerResolve(company=listing, method="unchanged")

    hit = extract_employer_heuristic(jd, listing_company=listing)
    method = "heuristic"
    if not hit:
        hit = await extract_employer_llm(
            listing_company=listing, title=title, url=url, jd=jd
        )
        method = "llm"
    if not hit or _norm_key(hit) == _norm_key(listing):
        return EmployerResolve(company=listing, method="unchanged")

    source = listing if (is_job_board_or_aggregator(listing) or is_staffing_firm(listing)) else None
    return EmployerResolve(
        company=hit,
        source_name=source,
        changed=True,
        method=method,
        confidence="high" if method == "heuristic" else "medium",
    )


def apply_employer_to_job_fields(job: Any, resolved: EmployerResolve) -> dict[str, Any]:
    """Mutate job company fields in-place. Returns a small change log."""
    if not resolved.changed:
        return {"changed": False, "company": job.company}
    old = job.company
    if resolved.source_name and not job.staffing_firm:
        job.staffing_firm = resolved.source_name
    elif is_job_board_or_aggregator(old) or is_staffing_firm(old):
        if not job.staffing_firm:
            job.staffing_firm = old
    job.company = resolved.company
    job.normalized_company = normalize_company(resolved.company)
    return {
        "changed": True,
        "from": old,
        "to": resolved.company,
        "staffing_firm": job.staffing_firm,
        "method": resolved.method,
    }
