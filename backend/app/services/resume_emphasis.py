"""Guarantee every experience bullet carries the JD's keywords in <strong>.

The prompt asks for this, and mostly gets it. Mostly is not enough: across three
generations of the same profile the model produced 50 emphasised terms, then 39,
then 10 — leaving 8, 11 and 31 bullets with no emphasis at all. A recruiter
skimming for the words in their own posting sees nothing on those lines, and no
amount of restating the rule made the count reliable; the instruction is simply
not something a model checks well across forty bullets.

So the rule is enforced here instead, the way enforce_resume_header() enforces
the header rather than trusting the prompt to reproduce it. The model still
chooses what to emphasise and usually does it well — this only fills in the
bullets it skipped, and trims any that ran long.

Opt-in per persona: set tailor.enforce_bullet_emphasis in the persona's yml.
"""
from __future__ import annotations

import re

MAX_PER_BULLET = 3

# Terms worth emphasising, as whole words. Practices sit alongside products on
# purpose — a bullet whose substance is schema validation or on-call ownership
# needs emphasis just as much as one naming Snowflake, and those are exactly the
# bullets the model leaves bare.
VOCAB = (
    # platforms and tools
    "PySpark", "Spark", "Databricks", "Snowflake", "BigQuery", "Redshift",
    "Synapse", "Airflow", "Dagster", "Prefect", "dbt", "Kafka", "Kinesis",
    "Event Hubs", "Pub/Sub", "Hadoop", "Terraform", "CloudFormation", "Bicep",
    "Ansible", "Docker", "Kubernetes", "Grafana", "Looker", "Tableau",
    "Power BI", "GitHub Actions", "Jenkins", "Azure DevOps", "MLflow",
    "SageMaker", "Delta Lake", "Unity Catalog", "Glue", "Step Functions",
    "Lambda", "ADLS", "Data Factory", "Key Vault", "Entra ID",
    # clouds and services
    "AWS", "Azure", "GCP", "Amazon S3", "S3", "RDS", "EC2", "IAM", "ADLS Gen2",
    # languages and interfaces
    "Python", "SQL", "Bash", "Shell", "REST APIs", "REST", "Linux", "TCP/IP",
    "DNS", "CLI tooling", "CLI", "ETL",
    # analyst and geospatial tooling — this vocabulary serves every persona,
    # and not all of them are data engineers
    # "GIS" is listed after the longer names, and _ORDERED sorts by length, so
    # QGIS and ArcGIS win where they appear. The boundary check stops the bare
    # term matching the GIS inside them.
    "QGIS", "ArcGIS", "PostGIS", "GIS", "Excel", "Google Sheets", "Sheets",
    "Jupyter", "pandas", "NumPy", "R", "MySQL", "PostgreSQL", "SQLite",
    # analyst and geospatial practices
    "pattern recognition", "statistical analysis", "significance testing",
    "trend analysis", "cohort analysis", "outlier detection", "deduplication",
    "record matching", "sampling", "audit passes", "QA/QC",
    "inter-annotator agreement", "annotation guidelines", "data annotation",
    "spatial validation", "topology checks", "geocoding", "digitizing",
    "map data", "dashboards", "reporting",
    # healthcare data standards and regulation — the terms a payer-domain
    # resume turns on, and none of them look like the tools above
    "HL7", "FHIR", "EDI", "EDI 837", "EDI 835", "837", "835", "270/271",
    "276/277", "HIPAA", "PHI", "PII", "CMS", "HCC", "ICD-10", "CPT",
    "claims adjudication", "risk adjustment", "prior authorization",
    "member eligibility", "medical claims", "PHI masking",
    # practices
    "data contracts", "schema evolution", "schema validation", "data quality",
    "data lineage", "data modeling", "dimensional modeling", "CDC",
    "incremental processing", "idempotent loads", "backfills", "reconciliation",
    "late-arriving data", "partitioning", "clustering", "query optimization",
    "orchestration", "CI/CD", "infrastructure as code", "unit tests",
    "integration tests", "regression testing", "automated testing",
    "structured logging", "observability", "monitoring", "alerting", "on-call",
    "incident response", "root-cause analysis", "access control", "RBAC",
    "least privilege", "PII masking", "encryption", "secrets management",
    "architecture review", "code review", "SLA", "SLO", "disaster recovery",
    "backup and recovery", "capacity planning", "cost optimization",
)

# Tried only when nothing in VOCAB matched. These are real engineering terms but
# weak emphasis — "ingestion" on its own tells a recruiter little — so they must
# never outrank a named tool or practice. They exist so that no bullet is left
# bare, which is the whole point of this module. A bullet that reached here is
# usually one phrased around an activity rather than a technology, e.g. "Added
# automated tests to telemetry ingestion, checking record counts before
# deployment".
LAST_RESORT = (
    "automated tests", "record counts", "data validation", "schema design",
    "indexing", "ingestion", "transformation", "deployment", "backups",
    "documentation", "runbooks", "file processing", "scheduled jobs",
    "scripting", "automation", "edge cases", "edge-case", "leadership",
    "guidelines", "escalation", "data flows", "production support",
    "production issues", "requirements",
)

_TAG = re.compile(r"<[^>]+>")
_LI = re.compile(r"(<li\b[^>]*>)(.*?)(</li>)", re.S | re.I)
_STRONG = re.compile(r"<strong\b[^>]*>(.*?)</strong>", re.S | re.I)
# Longest first, so "Amazon S3" is preferred over "S3" and "REST APIs" over "REST".
_ORDERED = tuple(sorted(VOCAB, key=len, reverse=True))


def _boundary(term: str) -> re.Pattern[str]:
    """Whole-word match that tolerates the punctuation real tool names carry.

    A plain \\b fails on "CI/CD" and "TCP/IP" — the slash is already a boundary,
    so \\b matches inside them and "CI" alone would hit. These lookarounds treat
    the whole token, punctuation included, as one word.
    """
    return re.compile(
        rf"(?<![0-9A-Za-z/+#.]){re.escape(term)}(?![0-9A-Za-z/+#.])", re.I
    )


def _text_of(fragment: str) -> str:
    return _TAG.sub(" ", fragment)


def jd_vocabulary(jd: str) -> list[str]:
    """The vocabulary terms the job description actually asks for."""
    return [term for term in _ORDERED if _boundary(term).search(jd or "")]


def _wrap_first(fragment: str, term: str) -> str | None:
    """Wrap the first occurrence of term that sits outside any tag."""
    pattern = _boundary(term)
    out: list[str] = []
    done = False
    last = 0
    pieces: list[tuple[bool, str]] = []
    for match in _TAG.finditer(fragment):
        if match.start() > last:
            pieces.append((False, fragment[last:match.start()]))
        pieces.append((True, match.group(0)))
        last = match.end()
    if last < len(fragment):
        pieces.append((False, fragment[last:]))

    for is_tag, piece in pieces:
        if is_tag or done:
            out.append(piece)
            continue
        found = pattern.search(piece)
        if not found:
            out.append(piece)
            continue
        out.append(
            piece[: found.start()]
            + f"<strong>{found.group(0)}</strong>"
            + piece[found.end():]
        )
        done = True
    return "".join(out) if done else None


def _trim(fragment: str, keep: int = MAX_PER_BULLET) -> str:
    """Unwrap emphasis past the cap, keeping the earliest ones."""
    seen = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal seen
        seen += 1
        return match.group(0) if seen <= keep else match.group(1)

    return _STRONG.sub(replace, fragment)


def emphasise_bullets(html: str, jd: str = "") -> str:
    """Give every <li> at least one and at most MAX_PER_BULLET <strong> terms.

    Bullets the model already emphasised are left as they are, apart from the
    cap. Preference goes to terms the job description itself names; a bullet with
    none of those falls back to its own strongest technical term, so the early
    roles are not left bare just because the posting never mentions TCP/IP.
    """
    wanted = set(jd_vocabulary(jd))

    def choose(visible: str) -> list[str]:
        """Best term first.

        A term the posting named always beats one it did not. Within a tier the
        earliest mention wins, because that is what the bullet is actually
        about — ranking by length instead picks whichever synonym happens to be
        spelled longer. Length only breaks a tie at the same position, which is
        how "Amazon S3" wins over the "S3" sitting inside it.
        """
        found = []
        for term in _ORDERED:
            match = _boundary(term).search(visible)
            if match:
                found.append((term not in wanted, match.start(), -len(term), term))
        if found:
            return [term for *_, term in sorted(found)]
        weak = []
        for term in sorted(LAST_RESORT, key=len, reverse=True):
            match = _boundary(term).search(visible)
            if match:
                weak.append((match.start(), -len(term), term))
        return [term for *_, term in sorted(weak)]

    def fix(match: re.Match[str]) -> str:
        open_tag, inner, close_tag = match.groups()
        count = len(_STRONG.findall(inner))
        if count > MAX_PER_BULLET:
            return open_tag + _trim(inner) + close_tag
        if count:
            return match.group(0)
        for term in choose(_text_of(inner)):
            wrapped = _wrap_first(inner, term)
            if wrapped:
                return open_tag + wrapped + close_tag
        return match.group(0)

    return _LI.sub(fix, html)
