"""Job tech-domain labels for fetch filters and the jobs table."""
from __future__ import annotations

import re
from typing import Any

# Two-letter codes shown in the UI. A job can match multiple overlapping domains.
JOB_DOMAINS: list[dict[str, Any]] = [
    {
        "id": "ai",
        "code": "AI",
        "label": "AI / ML",
        "blurb": "Machine learning, LLM, generative AI, MLOps",
        "patterns": [
            r"\bmachine learning\b",
            r"\bml engineer\b",
            r"\bmlops\b",
            r"\bllm\b",
            r"\bgenerative ai\b",
            r"\bgenai\b",
            r"\bdeep learning\b",
            r"\bneural network",
            r"\bcomputer vision\b",
            r"\bnlp\b",
            r"\bartificial intelligence\b",
            r"\bai engineer\b",
            r"\bai\/ml\b",
            r"\bpytorch\b",
            r"\btensorflow\b",
            r"\bhugging ?face\b",
            r"\blangchain\b",
            r"\brag\b",
        ],
    },
    {
        "id": "de",
        "code": "DE",
        "label": "Data Engineering",
        "blurb": "Pipelines, ETL/ELT, Spark, warehouses, streaming",
        "patterns": [
            r"\bdata engineer",
            r"\bdata engineering\b",
            r"\betl\b",
            r"\belt\b",
            r"\bapache spark\b",
            r"\bpyspark\b",
            r"\bdatabricks\b",
            r"\bairflow\b",
            r"\bkafka\b",
            r"\bdata pipeline",
            r"\bsnowflake\b",
            r"\bredshift\b",
            r"\bbigquery\b",
            r"\bdbt\b",
            r"\bdelta lake\b",
            r"\bdata platform\b",
        ],
    },
    {
        "id": "da",
        "code": "DA",
        "label": "Data Analyst",
        "blurb": "Analytics, BI, reporting, dashboards",
        "patterns": [
            r"\bdata analyst\b",
            r"\bbusiness analyst\b",
            r"\banalytics engineer\b",
            r"\bbi developer\b",
            r"\bbi analyst\b",
            r"\btableau\b",
            r"\bpower bi\b",
            r"\blooker\b",
            r"\breporting analyst\b",
        ],
    },
    {
        "id": "ds",
        "code": "DS",
        "label": "Data Scientist",
        "blurb": "Statistical modeling, experimentation, applied science",
        "patterns": [
            r"\bdata scientist\b",
            r"\bdata science\b",
            r"\bresearch scientist\b",
            r"\bapplied scientist\b",
            r"\bstatistician\b",
            r"\bcausal inference\b",
            r"\bexperimentation\b",
        ],
    },
    {
        "id": "fs",
        "code": "FS",
        "label": "Full Stack",
        "blurb": "End-to-end web apps, frontend + backend",
        "patterns": [
            r"\bfull[\s-]?stack\b",
            r"\bfullstack\b",
        ],
    },
    {
        "id": "be",
        "code": "BE",
        "label": "Backend",
        "blurb": "APIs, services, server-side platforms",
        "patterns": [
            r"\bback[\s-]?end\b",
            r"\bbackend\b",
            r"\bapi engineer\b",
            r"\bplatform engineer\b",
            r"\bsoftware engineer.?backend\b",
            r"\bmicroservices\b",
        ],
    },
    {
        "id": "fe",
        "code": "FE",
        "label": "Frontend",
        "blurb": "UI, React, web client engineering",
        "patterns": [
            r"\bfront[\s-]?end\b",
            r"\bfrontend\b",
            r"\bui engineer\b",
            r"\breact developer\b",
            r"\bui\/ux engineer\b",
        ],
    },
    {
        "id": "ce",
        "code": "CE",
        "label": "Cloud Engineer",
        "blurb": "AWS/GCP/Azure infrastructure and cloud platforms",
        "patterns": [
            r"\bcloud engineer\b",
            r"\bcloud architect\b",
            r"\bsite reliability\b",
            r"\baws engineer\b",
            r"\bgcp engineer\b",
            r"\bazure engineer\b",
            r"\binfrastructure engineer\b",
            r"\bcloud platform\b",
        ],
    },
    {
        "id": "sq",
        "code": "SQ",
        "label": "SQL Developer",
        "blurb": "SQL, stored procedures, warehouse SQL work",
        "patterns": [
            r"\bsql developer\b",
            r"\bsql engineer\b",
            r"\bdatabase developer\b",
            r"\bstored procedure",
            r"\bssis\b",
            r"\bt[\s-]?sql\b",
            r"\bpl[\s/]?sql\b",
        ],
    },
    {
        "id": "sf",
        "code": "SF",
        "label": "Salesforce",
        "blurb": "Salesforce CRM, Apex, LWC, CPQ, clouds",
        "patterns": [
            r"\bsalesforce\b",
            r"\bapex\b",
            r"\blwc\b",
            r"\blightning web component",
            r"\bsalesforce cpq\b",
            r"\brevenue cloud\b",
            r"\bservice cloud\b",
            r"\bsales cloud\b",
            r"\bexperience cloud\b",
            r"\bmulesoft\b",
            r"\bnCino\b",
        ],
    },
    {
        "id": "dv",
        "code": "DV",
        "label": "DevOps / SRE",
        "blurb": "CI/CD, Kubernetes, reliability, platform ops",
        "patterns": [
            r"\bdevops\b",
            r"\bsre\b",
            r"\bkubernetes\b",
            r"\bplatform ops\b",
            r"\bci\/cd\b",
            r"\bterraform\b",
            r"\binfrastructure as code\b",
        ],
    },
    {
        "id": "se",
        "code": "SE",
        "label": "Software Engineer",
        "blurb": "General software / application engineering",
        "patterns": [
            r"\bsoftware engineer\b",
            r"\bsoftware developer\b",
            r"\bapplication developer\b",
            r"\bapplication engineer\b",
            r"\bprogrammer\b",
        ],
    },
]

_DOMAIN_BY_ID = {d["id"]: d for d in JOB_DOMAINS}
_DOMAIN_BY_CODE = {d["code"].upper(): d for d in JOB_DOMAINS}


def list_job_domains() -> list[dict[str, Any]]:
    return [
        {
            "id": d["id"],
            "code": d["code"],
            "label": d["label"],
            "blurb": d.get("blurb") or "",
        }
        for d in JOB_DOMAINS
    ]


def normalize_domain_ids(raw: list[str] | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = str(item or "").strip().lower()
        if not key:
            continue
        domain = _DOMAIN_BY_ID.get(key) or _DOMAIN_BY_CODE.get(key.upper())
        if not domain:
            continue
        did = domain["id"]
        if did in seen:
            continue
        seen.add(did)
        out.append(did)
    return out


def classify_domains(title: str | None, description: str | None = None) -> list[str]:
    """Return matching domain ids (may be multiple). Falls back to se when IT-ish."""
    text = f"{title or ''}\n{(description or '')[:2500]}"
    hits: list[tuple[int, str]] = []
    for domain in JOB_DOMAINS:
        if domain["id"] == "se":
            continue  # apply as fallback only
        score = 0
        for pat in domain["patterns"]:
            if re.search(pat, text, re.I):
                score += 1
        if score:
            # Title hits weigh more: re-check patterns against title alone
            title_hits = sum(1 for pat in domain["patterns"] if re.search(pat, title or "", re.I))
            hits.append((score + title_hits * 2, domain["id"]))
    hits.sort(key=lambda x: (-x[0], x[1]))
    ids = [did for _, did in hits]
    if not ids:
        for pat in _DOMAIN_BY_ID["se"]["patterns"]:
            if re.search(pat, text, re.I):
                return ["se"]
        return []
    # Keep overlapping labels when they scored; cap at 3 for UI brevity
    return ids[:3]


def domains_to_codes(domain_ids: list[str]) -> list[str]:
    codes: list[str] = []
    for did in domain_ids:
        d = _DOMAIN_BY_ID.get(did)
        if d:
            codes.append(d["code"])
    return codes


def domains_storage(domain_ids: list[str]) -> str | None:
    codes = domains_to_codes(domain_ids)
    return ",".join(codes) if codes else None


def parse_stored_domains(value: str | None) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    for part in str(value).replace(";", ",").split(","):
        code = part.strip().upper()
        if not code:
            continue
        d = _DOMAIN_BY_CODE.get(code) or _DOMAIN_BY_ID.get(code.lower())
        if d and d["code"] not in out:
            out.append(d["code"])
    return out


def matches_allowed(domain_ids: list[str], allowed_ids: list[str] | None) -> bool:
    """OR match: job kept if it hits any selected domain. Empty allowed = no filter."""
    if not allowed_ids:
        return True
    if not domain_ids:
        return False
    allowed = set(allowed_ids)
    return any(d in allowed for d in domain_ids)
