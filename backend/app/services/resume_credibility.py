"""Does this resume read as true?

Separate question from the ATS score. An ATS filter asks whether the keywords
are present; a human reviewer asks whether the document holds together — and a
tailored resume is generated fresh for every posting, so nobody reads the 1,400
that go out. This reads them.

Everything here is checked against the resume's own text, not against the
persona prompt. The strongest signal is anachronism: a bullet claiming
Databricks at a job that ended in 2012 is checkably false, and no amount of
keyword coverage makes it survive an interview.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Year a technology became something you could plausibly have used at work.
# Deliberately generous — the first public release, not mainstream adoption —
# so a flag means the claim is hard to defend, not merely early.
TECH_AVAILABLE_FROM: dict[str, int] = {
    "hadoop": 2006,
    "scala": 2004,
    "go": 2009,
    "bigquery": 2010,
    "spark": 2010,
    "kafka": 2011,
    "redshift": 2012,
    "looker": 2012,
    "typescript": 2012,
    "docker": 2013,
    "databricks": 2013,
    "kubernetes": 2014,
    "terraform": 2014,
    "snowflake": 2014,
    "flink": 2014,
    "graphql": 2015,
    "airflow": 2015,
    "power bi": 2015,
    "tensorflow": 2015,
    "dbt": 2016,
    "athena": 2016,
    "clickhouse": 2016,
    "pytorch": 2016,
    "glue": 2017,
    "sagemaker": 2017,
    "mlflow": 2018,
    "dagster": 2018,
    "prefect": 2018,
    "duckdb": 2019,
    "synapse": 2019,
    "data lake": 2019,  # "lakehouse"/"delta lake" framing
    "weaviate": 2019,
    "llm": 2020,
    "rag": 2020,
    "vertex ai": 2021,
    "pinecone": 2021,
    "qdrant": 2021,
    "databricks ml": 2021,  # unity catalog
    "langchain": 2022,
    "ai": 2015,
    "fabric": 2023,
}

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "Feb 2023 - Present", "Oct 2021 – May 2022", "2019 - 2021"
_DATE_RANGE_RE = re.compile(
    r"(?P<start_mon>[a-z]{3,9})?\.?\s*(?P<start_year>(?:19|20)\d{2})"
    r"\s*[-–—to]{1,3}\s*"
    r"(?P<end>present|current|now|(?:(?P<end_mon>[a-z]{3,9})?\.?\s*(?P<end_year>(?:19|20)\d{2})))",
    re.I,
)

# Where the experience section ends. A skills list is a claim about a career,
# not about the job it happens to sit under.
# Match the whole heading line, not a prefix: real resumes write "SELECTED
# PROJECTS" and "TECHNICAL SKILLS", and anchoring on the bare keyword missed
# both — which attributed every project's tech stack to the oldest job listed.
_SECTION_HEADING_RE = re.compile(
    r"^[ \t]*(?=[^\n]{0,48}$)[^\n]*?\b("
    r"skills|competenc\w+|education|certification\w*|project\w*|publications?"
    r"|awards?|languages|interests|summary|profile|training|coursework"
    r"|volunteer|references|patents?"
    r")\b[^\n]*$",
    re.I | re.M,
)

_PERCENT_RE = re.compile(r"(\d{1,4}(?:\.\d+)?)\s*%")

# A percentage that describes a *change*, not a level. "reduced runtime by 95%"
# is a claim worth questioning; "99.9% availability" is a normal SLA.
# "by N%" is the size of a change. "to N%" is the level reached — "improved
# SLA compliance to 99.7%" is not a 99.7% improvement, and treating it as one
# flagged a third of the resumes for something they did not say.
_IMPROVEMENT_RE = re.compile(
    r"\b(?:improv\w*|reduc\w*|increas\w*|decreas\w*|cut|cutting|lower\w*|boost\w*"
    r"|acceler\w*|shrink\w*|sav\w*|faster|slash\w*)\b(?:(?!\bto\b)[^.]){0,60}?"
    r"\bby\s+(?:about|roughly|approximately|around|nearly|over|up\s+to|~)?\s*"
    r"(?P<pct>\d{1,4}(?:\.\d+)?)\s*%",
    re.I,
)
_MULTIPLE_RE = re.compile(r"(\d{1,4}(?:\.\d+)?)\s*x\b", re.I)

# Hedged numbers read as honest; a page of exact figures reads as invented.
_HEDGE_WORDS = ("approximately", "roughly", "about", "around", "~", "over", "nearly", "up to")


@dataclass
class Finding:
    """One concrete problem, with the evidence that produced it."""

    severity: str  # "high" | "medium" | "low"
    kind: str
    message: str
    evidence: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "evidence": self.evidence,
        }


@dataclass
class CredibilityReport:
    score: int
    findings: list[Finding] = field(default_factory=list)
    roles: int = 0
    span_years: float = 0.0
    distinct_tech: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "roles": self.roles,
            "span_years": round(self.span_years, 1),
            "distinct_tech": self.distinct_tech,
            "findings": [f.as_dict() for f in self.findings],
        }


def resume_text(raw: str | None) -> str:
    """Readable text from the generated HTML, with the stylesheet dropped."""
    if not raw:
        return ""
    body = raw.split("</style>")[-1] if "</style>" in raw else raw
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<br\s*/?>|</(p|li|div|h[1-6]|tr)>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = (
        body.replace("&nbsp;", " ")
        .replace("&middot;", "·")
        .replace("&amp;", "&")
        .replace("&#8211;", "-")
    )
    body = re.sub(r"[ \t]+", " ", body)
    return re.sub(r"\n\s*\n+", "\n", body).strip()


@dataclass
class ParsedRole:
    start_year: int
    end_year: int
    ongoing: bool
    text: str
    header: str


def parse_roles(text: str) -> list[ParsedRole]:
    """Split the experience section into dated blocks.

    Each date range owns the text up to the next one, or to the next section
    heading — without that stop the final role absorbs TECHNICAL SKILLS, and
    every tool in the skills list gets attributed to the oldest job on the
    resume. That produced three false anachronisms on the first document tried.
    """
    matches = list(_DATE_RANGE_RE.finditer(text))
    roles: list[ParsedRole] = []
    this_year = date.today().year
    for index, match in enumerate(matches):
        start_year = int(match.group("start_year"))
        ongoing = bool(re.match(r"present|current|now", match.group("end") or "", re.I))
        end_year = this_year if ongoing else int(match.group("end_year") or start_year)
        if not (1970 <= start_year <= this_year + 1):
            continue
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = _SECTION_HEADING_RE.search(text, body_start, body_end)
        if section:
            body_end = section.start()
        header = text[max(0, match.start() - 120) : match.start()].strip().splitlines()
        roles.append(
            ParsedRole(
                start_year=start_year,
                end_year=end_year,
                ongoing=ongoing,
                text=text[body_start:body_end],
                header=header[-1].strip() if header else "",
            )
        )
    return roles


def _claimed_years(text: str) -> int | None:
    """The tenure the summary claims for itself."""
    match = re.search(r"(\d{1,2})\s*\+?\s*years?\s+of\s+(?:\w+\s+){0,3}experience", text, re.I)
    return int(match.group(1)) if match else None


def check_anachronisms(roles: list[ParsedRole]) -> list[Finding]:
    """Technology claimed at a job that ended before the technology existed."""
    from app.services.ats_score import extract_terms

    findings: list[Finding] = []
    for role in roles:
        for term in sorted(extract_terms(role.text)):
            available = TECH_AVAILABLE_FROM.get(term)
            if available is None or role.end_year >= available:
                continue
            findings.append(
                Finding(
                    severity="high",
                    kind="anachronism",
                    message=(
                        f"{term} is claimed in a role ending {role.end_year}, "
                        f"but it did not exist until {available}"
                    ),
                    evidence=role.header[:90],
                )
            )
    return findings


def check_timeline(text: str, roles: list[ParsedRole]) -> list[Finding]:
    findings: list[Finding] = []
    if not roles:
        findings.append(
            Finding("high", "structure", "no dated roles could be parsed from the resume")
        )
        return findings

    span = max(r.end_year for r in roles) - min(r.start_year for r in roles)
    claimed = _claimed_years(text)
    if claimed is not None and span and claimed - span >= 3:
        findings.append(
            Finding(
                "high",
                "timeline",
                f"summary claims {claimed} years but the dated roles cover about {span}",
                evidence=f"{min(r.start_year for r in roles)}–{max(r.end_year for r in roles)}",
            )
        )

    # Overlapping employment reads as an error or as padding.
    ordered = sorted(roles, key=lambda r: r.start_year)
    for earlier, later in zip(ordered, ordered[1:]):
        if later.start_year < earlier.end_year - 1 and not earlier.ongoing:
            findings.append(
                Finding(
                    "medium",
                    "timeline",
                    f"roles overlap: one runs to {earlier.end_year}, the next starts {later.start_year}",
                    evidence=later.header[:90],
                )
            )
            break
    return findings


def check_claims(text: str, roles: list[ParsedRole]) -> list[Finding]:
    """Metrics that would not survive being asked about."""
    findings: list[Finding] = []
    # Only *changes* are suspicious at 90%+. "99.9% availability" and "98%
    # accuracy" are levels, and they are the most ordinary thing on a data
    # engineering resume — flagging those made a third of the set look bad.
    extreme: list[str] = []
    for match in _IMPROVEMENT_RE.finditer(text):
        value = float(match.group("pct"))
        if value >= 90:
            extreme.append(match.group(0).strip())
    if extreme:
        findings.append(
            Finding(
                "medium",
                "metrics",
                f"{len(extreme)} improvement claim(s) of 90% or more",
                evidence="; ".join(e[:60] for e in extreme[:3]),
            )
        )
    multiples = [float(m) for m in _MULTIPLE_RE.findall(text)]
    if any(m >= 20 for m in multiples):
        findings.append(
            Finding(
                "medium",
                "metrics",
                "a speedup of 20x or more is claimed",
                evidence=", ".join(f"{m:g}x" for m in multiples if m >= 20),
            )
        )

    bullets = [b.strip() for b in text.splitlines() if len(b.strip()) > 40]
    with_numbers = [b for b in bullets if _PERCENT_RE.search(b) or _MULTIPLE_RE.search(b)]
    if bullets and len(with_numbers) / len(bullets) > 0.8 and len(bullets) >= 6:
        findings.append(
            Finding(
                "low",
                "metrics",
                "almost every bullet carries a number — reads as generated",
                evidence=f"{len(with_numbers)} of {len(bullets)} bullets",
            )
        )
    hedged = sum(1 for b in with_numbers if any(h in b.lower() for h in _HEDGE_WORDS))
    if with_numbers and hedged == 0 and len(with_numbers) >= 5:
        findings.append(
            Finding(
                "low",
                "metrics",
                "every figure is stated exactly, with no estimate wording",
            )
        )
    return findings


def check_breadth(text: str, roles: list[ParsedRole]) -> tuple[list[Finding], int]:
    """More tools than a career can hold."""
    from app.services.ats_score import extract_terms

    findings: list[Finding] = []
    terms = extract_terms(text)
    span = (
        max(r.end_year for r in roles) - min(r.start_year for r in roles) if roles else 0
    )
    if len(terms) > 45:
        findings.append(
            Finding(
                "medium",
                "breadth",
                f"{len(terms)} distinct technologies named — more than a resume can defend",
            )
        )
    elif span and len(terms) / max(span, 1) > 6:
        findings.append(
            Finding(
                "low",
                "breadth",
                f"{len(terms)} technologies across roughly {span} years is dense",
            )
        )
    return findings, len(terms)


def check_repetition(roles: list[ParsedRole]) -> list[Finding]:
    """The same achievement pasted under two different employers."""
    seen: dict[str, str] = {}
    for role in roles:
        for line in role.text.splitlines():
            clean = re.sub(r"[^a-z ]", "", line.lower()).strip()
            if len(clean) < 45:
                continue
            key = " ".join(clean.split()[:9])
            if key in seen and seen[key] != role.header:
                return [
                    Finding(
                        "medium",
                        "repetition",
                        "the same bullet appears under two employers",
                        evidence=line.strip()[:90],
                    )
                ]
            seen[key] = role.header
    return []


_PENALTY = {"high": 22, "medium": 9, "low": 4}


def score_credibility(raw_resume: str | None) -> CredibilityReport:
    """0–100: how well the resume holds together as a truthful document."""
    text = resume_text(raw_resume)
    if not text:
        return CredibilityReport(
            score=0, findings=[Finding("high", "structure", "resume is empty")]
        )

    roles = parse_roles(text)
    findings: list[Finding] = []
    findings += check_anachronisms(roles)
    findings += check_timeline(text, roles)
    findings += check_claims(text, roles)
    breadth_findings, distinct = check_breadth(text, roles)
    findings += breadth_findings
    findings += check_repetition(roles)

    score = 100
    for finding in findings:
        score -= _PENALTY.get(finding.severity, 4)
    span = (
        max(r.end_year for r in roles) - min(r.start_year for r in roles) if roles else 0
    )
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f.severity, 3))
    return CredibilityReport(
        score=max(0, min(100, score)),
        findings=findings,
        roles=len(roles),
        span_years=float(span),
        distinct_tech=distinct,
    )
