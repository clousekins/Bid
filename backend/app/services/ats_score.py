"""Score a resume against a job description the way an ATS keyword filter would.

Deliberately deterministic — no LLM call. Scoring runs on every job in the
table and on every regeneration attempt, so it has to be free, instant and
give the same answer twice. Every score ships with the breakdown that produced
it; an unexplained number is not actionable.

Two different questions, two different scores:

  technical_feasibility(persona, job)  — before any resume exists, can this
      person plausibly do this job? Triage: skip the ones they cannot.
  score_resume(resume_text, jd)        — after tailoring, would the document
      survive a keyword filter for this posting?
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

# Canonical tech vocabulary with the spellings that appear in the wild.
# Matching on a curated list beats raw tokenisation: "go" and "r" are real
# languages and would otherwise match half the English in a JD.
TECH_TERMS: dict[str, tuple[str, ...]] = {
    # languages
    "python": ("python",),
    "sql": ("sql",),
    "java": ("java",),
    "scala": ("scala",),
    "go": ("golang", "go lang"),
    # "r" alone is unmatchable without false positives; only the explicit forms.
    "r": ("r language", "rstudio", "r programming"),
    "typescript": ("typescript", "ts"),
    "javascript": ("javascript",),
    "c#": ("c#", "csharp", ".net"),
    "bash": ("bash", "shell scripting"),
    # data engineering
    "spark": ("spark", "pyspark", "apache spark"),
    "hadoop": ("hadoop", "hdfs", "mapreduce"),
    "kafka": ("kafka",),
    "flink": ("flink",),
    "airflow": ("airflow",),
    "dbt": ("dbt",),
    "dagster": ("dagster",),
    "prefect": ("prefect",),
    "etl": ("etl", "elt"),
    "streaming": ("streaming", "real-time data", "realtime data"),
    "data modeling": ("data modeling", "data modelling", "dimensional model", "star schema"),
    "data warehouse": ("data warehouse", "data warehousing"),
    "data lake": ("data lake", "lakehouse", "delta lake"),
    "data quality": ("data quality", "data governance", "data lineage"),
    "cdc": ("change data capture", "cdc"),
    # warehouses / stores
    "snowflake": ("snowflake",),
    "databricks": ("databricks",),
    "bigquery": ("bigquery", "big query"),
    "redshift": ("redshift",),
    "synapse": ("synapse",),
    "postgres": ("postgres", "postgresql"),
    "mysql": ("mysql",),
    "mongodb": ("mongodb", "mongo"),
    "cassandra": ("cassandra",),
    "elasticsearch": ("elasticsearch", "opensearch"),
    "redis": ("redis",),
    "clickhouse": ("clickhouse",),
    "duckdb": ("duckdb",),
    # cloud
    "aws": ("aws", "amazon web services"),
    "azure": ("azure",),
    "gcp": ("gcp", "google cloud"),
    "s3": ("s3",),
    "lambda": ("aws lambda", "lambda function"),
    "glue": ("aws glue", "glue job"),
    "emr": ("emr",),
    "athena": ("athena",),
    "step functions": ("step functions",),
    "kinesis": ("kinesis",),
    "dynamodb": ("dynamodb",),
    "fabric": ("microsoft fabric",),
    # platform / infra
    "docker": ("docker", "container"),
    "kubernetes": ("kubernetes", "k8s", "eks", "aks", "gke"),
    "terraform": ("terraform",),
    "ci/cd": ("ci/cd", "cicd", "continuous integration", "continuous delivery"),
    "git": ("git", "github", "gitlab"),
    "linux": ("linux", "unix"),
    "microservices": ("microservice",),
    "rest api": ("rest api", "restful", "api development"),
    "graphql": ("graphql",),
    # analytics / BI
    "tableau": ("tableau",),
    "power bi": ("power bi", "powerbi"),
    "looker": ("looker",),
    "excel": ("excel",),
    "pandas": ("pandas",),
    "numpy": ("numpy",),
    # ML / AI
    "machine learning": ("machine learning", "ml model"),
    "deep learning": ("deep learning", "neural network"),
    "pytorch": ("pytorch",),
    "tensorflow": ("tensorflow",),
    "scikit-learn": ("scikit-learn", "sklearn"),
    "mlops": ("mlops", "ml ops"),
    "ai": ("ai", "artificial intelligence", "ai/ml"),
    "llm": ("llm", "large language model", "gpt", "generative ai", "genai"),
    "rag": ("retrieval augmented", "rag pipeline"),
    "langchain": ("langchain", "llamaindex"),
    "vector database": ("vector database", "vector db", "pinecone", "weaviate", "qdrant"),
    "nlp": ("nlp", "natural language processing"),
    "computer vision": ("computer vision",),
    "feature engineering": ("feature engineering", "feature store"),
    "mlflow": ("mlflow",),
    "sagemaker": ("sagemaker",),
    "vertex ai": ("vertex ai",),
    "databricks ml": ("mosaic", "unity catalog"),
    # practice
    "agile": ("agile", "scrum"),
    "stakeholder": ("stakeholder",),
    "mentoring": ("mentor", "mentoring"),
    "architecture": ("architecture", "system design"),
}

# Sections an ATS parser expects to find. Missing ones cost structure points.
_EXPECTED_SECTIONS = (
    ("experience", ("experience", "employment", "work history")),
    ("education", ("education",)),
    ("skills", ("skills", "technical skills", "core competencies")),
)

# Phrases that mark a genuine requirement rather than a passing mention.
_REQUIREMENT_CUES = (
    "required",
    "requirement",
    "must have",
    "you have",
    "you will need",
    "qualifications",
    "we're looking for",
    "we are looking for",
    "minimum",
    "proficient",
    "experience with",
    "experience in",
    "strong ",
    "expertise",
)


@dataclass
class ScoreBreakdown:
    """Why a score is what it is. Shown in the UI; never just the number."""

    score: int
    keyword_points: int
    structure_points: int
    title_points: int
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "keyword_points": self.keyword_points,
            "structure_points": self.structure_points,
            "title_points": self.title_points,
            "matched": self.matched,
            "missing": self.missing,
            "notes": self.notes,
        }


def _normalize(text: str | None) -> str:
    body = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", body).lower()


def _split_terms() -> tuple[dict[str, str], list[tuple[str, tuple[re.Pattern[str], ...]]]]:
    """Split the vocabulary into a word lookup and the few that need regex.

    Most aliases are a single word, and those can be answered by set membership
    against the document's tokens — one pass over the text instead of one pass
    per term. Only multi-word or punctuated aliases ("power bi", "ci/cd", "c#")
    still need a scan, and there are about thirty of those.
    """
    word_to_term: dict[str, str] = {}
    regex_terms: list[tuple[str, tuple[re.Pattern[str], ...]]] = []
    for canonical, aliases in TECH_TERMS.items():
        patterns: list[re.Pattern[str]] = []
        for alias in (canonical, *aliases):
            token = alias.strip()
            if not token:
                continue
            if token.isalnum():
                # First writer wins; aliases never collide across terms today.
                word_to_term.setdefault(token, canonical)
            else:
                patterns.append(re.compile(re.escape(token)))
        if patterns:
            regex_terms.append((canonical, tuple(patterns)))
    return word_to_term, regex_terms


_WORD_TERMS, _REGEX_TERMS = _split_terms()
# Tokens keep +, # and . so "c#", "ci/cd" halves and "scikit-learn" survive.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#]*")


def extract_terms(text: str | None) -> set[str]:
    """Canonical tech terms present in a block of text."""
    body = _normalize(text)
    if not body:
        return set()
    found: set[str] = set()
    for token in _TOKEN_RE.findall(body):
        canonical = _WORD_TERMS.get(token)
        if canonical is not None:
            found.add(canonical)
    for canonical, patterns in _REGEX_TERMS:
        if canonical in found:
            continue
        for pattern in patterns:
            if pattern.search(body):
                found.add(canonical)
                break
    return found


@lru_cache(maxsize=512)
def _jd_requirements_cached(jd: str) -> tuple[frozenset[str], frozenset[str]]:
    return _jd_requirements_uncached(jd)


def _jd_requirements_uncached(jd: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """Split a JD's tech terms into (hard requirements, everything mentioned).

    Terms appearing in a sentence with a requirement cue weigh more than ones
    dropped into a company blurb.
    """
    body = _normalize(jd)
    if not body:
        return frozenset(), frozenset()
    everything = extract_terms(body)
    # Scan the requirement sentences as one block. Running the full term list
    # per sentence made this the slowest thing on the jobs page.
    cue_text = " . ".join(
        sentence
        for sentence in re.split(r"[.;\n•·]|(?<=\))\s", body)
        if any(cue in sentence for cue in _REQUIREMENT_CUES)
    )
    required = extract_terms(cue_text) if cue_text else set()
    # A JD with no recognisable cue sentences still has real requirements.
    return frozenset(required or everything), frozenset(everything)


def jd_requirements(jd: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """Cached: the same JD is scored on every page load and every retry."""
    if not jd:
        return frozenset(), frozenset()
    return _jd_requirements_cached(jd)


def _structure_points(resume_text: str | None) -> tuple[int, list[str]]:
    """Up to 20 points for being parseable by an ATS at all."""
    raw = resume_text or ""
    body = _normalize(raw)
    points = 20
    notes: list[str] = []
    if not body:
        return 0, ["resume is empty"]

    for label, aliases in _EXPECTED_SECTIONS:
        if not any(alias in body for alias in aliases):
            points -= 4
            notes.append(f"no clear {label} section")

    if not re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", body):
        points -= 4
        notes.append("no email address found")
    if not re.search(r"\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}", body):
        points -= 2
        notes.append("no phone number found")

    # Tables and images are the classic ATS parse failure.
    if re.search(r"<table|<img", raw, re.I):
        points -= 4
        notes.append("contains a table or image — many parsers drop these")

    words = len(body.split())
    if words < 250:
        points -= 4
        notes.append(f"very short ({words} words)")
    elif words > 1400:
        points -= 2
        notes.append(f"very long ({words} words)")

    return max(0, points), notes


def _title_points(resume_text: str | None, job_title: str | None) -> tuple[int, list[str]]:
    """Up to 15 points for echoing the posting's title language."""
    title = _normalize(job_title)
    body = _normalize(resume_text)
    if not title or not body:
        return 0, []
    stop = {
        "and", "or", "the", "a", "an", "of", "for", "to", "in", "with", "at",
        "senior", "staff", "lead", "principal", "sr", "jr", "ii", "iii", "remote",
    }
    words = [w for w in re.findall(r"[a-z][a-z+#.]{1,}", title) if w not in stop]
    if not words:
        return 15, []
    hits = sum(1 for w in words if w in body)
    ratio = hits / len(words)
    missing = [w for w in words if w not in body]
    notes = [] if ratio >= 0.6 else [f"title words absent from resume: {', '.join(missing[:5])}"]
    return round(15 * ratio), notes


def score_resume(
    resume_text: str | None,
    jd: str | None,
    *,
    job_title: str | None = None,
) -> ScoreBreakdown:
    """0–100 estimate of surviving an ATS keyword filter for this posting."""
    required, mentioned = jd_requirements(jd)
    have = extract_terms(resume_text)

    if required:
        # Hard requirements carry the weight; nice-to-haves top it up.
        hard_hit = len(required & have) / len(required)
        soft_pool = mentioned - required
        soft_hit = (len(soft_pool & have) / len(soft_pool)) if soft_pool else hard_hit
        keyword_ratio = 0.8 * hard_hit + 0.2 * soft_hit
    else:
        keyword_ratio = 1.0 if resume_text else 0.0

    keyword_points = round(65 * keyword_ratio)
    structure_points, structure_notes = _structure_points(resume_text)
    title_points, title_notes = _title_points(resume_text, job_title)

    matched = sorted(required & have)
    missing = sorted(required - have)
    notes = structure_notes + title_notes
    if missing:
        notes.insert(0, f"{len(missing)} required term(s) not in the resume")

    return ScoreBreakdown(
        score=max(0, min(100, keyword_points + structure_points + title_points)),
        keyword_points=keyword_points,
        structure_points=structure_points,
        title_points=title_points,
        matched=matched,
        missing=missing,
        notes=notes,
    )


# Words that appear in every other posting and say nothing about the field.
# "Engineer" matching "Engineer" is not evidence that the roles are related.
_GENERIC_TITLE_WORDS = frozenset(
    {
        "senior", "sr", "junior", "jr", "staff", "principal", "lead", "head",
        "engineer", "engineering", "developer", "architect", "manager",
        "analyst", "specialist", "consultant", "administrator", "director",
        "associate", "expert", "professional", "officer", "technician",
        "remote", "hybrid", "onsite", "contract", "fulltime", "part", "time",
        "i", "ii", "iii", "iv", "and", "or", "the", "of", "for", "to", "in",
        "with", "at", "a", "an", "level", "role", "position", "job", "us",
    }
)


def _persona_role_words(persona: Any) -> frozenset[str]:
    """The distinctive words from titles this persona has actually held."""
    titles = [str(getattr(persona, "default_professional_title", "") or "")]
    for role in getattr(persona, "experience_ground_truth", None) or []:
        if isinstance(role, dict):
            titles.append(str(role.get("title") or ""))
    words = set()
    for title in titles:
        for word in re.findall(r"[a-z][a-z+#.]*", title.lower()):
            if word not in _GENERIC_TITLE_WORDS:
                words.add(word)
    return frozenset(words)


def _role_alignment(persona: Any, job_title: str | None) -> tuple[float, str]:
    """How much the posting's job family overlaps this persona's own.

    Generic words are stripped first: "Electrical Engineer" and "Data
    Engineer" share only "engineer", which is no evidence at all.
    """
    title = (job_title or "").lower()
    words = [w for w in re.findall(r"[a-z][a-z+#.]*", title) if w not in _GENERIC_TITLE_WORDS]
    if not words:
        # Nothing but generic words — no signal either way, so don't punish it.
        return 0.5, ""
    mine = _persona_role_words(persona)
    known = persona_terms(persona)

    # A term made of several words only counts when the title carries the
    # whole thing. Splitting them apart let one ordinary English word answer
    # for a tool: "power", taken out of "Power BI", matched "Power Generation
    # Electrical Engineer" and stopped that posting reading as another field.
    #
    # Nothing is lost by being strict here — the tools a title actually names
    # (spark, airflow, databricks, snowflake, kafka, tableau) are single words
    # already, so they still match on their own.
    solo = {term for term in known if not re.search(r"[ /]", term)}
    compound = [term for term in known if term not in solo and term in title]

    # Score by how much of the title is accounted for, counting each word once.
    # A compound term earns every word it covers — "Machine Learning Engineer"
    # is fully explained by knowing machine learning, not half explained.
    covered = {w for w in words if w in mine or w in solo}
    for term in compound:
        covered.update(part for part in re.split(r"[ /]", term) if part)

    hits = [w for w in words if w in covered]
    if not hits:
        return 0.0, f"title is a different field: {' '.join(words[:4])}"
    return len(hits) / len(words), ""


def persona_corpus(persona: Any) -> str:
    """Everything we can truthfully say this persona has done.

    Feasibility must be judged against real history, not against whatever a
    tailored resume could be talked into claiming.
    """
    parts: list[str] = [
        str(getattr(persona, "default_professional_title", "") or ""),
        str(getattr(persona, "total_experience", "") or ""),
    ]
    for role in getattr(persona, "experience_ground_truth", None) or []:
        if not isinstance(role, dict):
            continue
        parts += [str(role.get(k) or "") for k in ("title", "focus", "allowed_tech", "company")]
    for cert in getattr(persona, "raw", {}).get("tailor", {}).get(
        "certifications_ground_truth", []
    ) or []:
        parts.append(str(cert if isinstance(cert, str) else cert.get("name", "")))
    skills = getattr(persona, "skills_ground_truth", None) or {}
    if isinstance(skills, dict):
        for value in skills.values():
            parts.append(", ".join(value) if isinstance(value, list) else str(value))
    parts.append(str(getattr(persona, "system_prompt", "") or "")[:40000])
    return " \n".join(p for p in parts if p)


_PERSONA_TERMS: dict[int, frozenset[str]] = {}


def persona_terms(persona: Any) -> frozenset[str]:
    """Tech terms this persona can evidence.

    Keyed on the persona object's identity, not its text: the corpus is 41KB
    and hashing it as a cache key on every table row cost more than the scan
    it was meant to avoid. load_persona() returns a cached instance, so the
    same persona is the same object across a request.
    """
    key = id(persona)
    hit = _PERSONA_TERMS.get(key)
    if hit is None:
        hit = frozenset(extract_terms(persona_corpus(persona)))
        if len(_PERSONA_TERMS) > 64:
            _PERSONA_TERMS.clear()
        _PERSONA_TERMS[key] = hit
    return hit


def technical_feasibility(persona: Any, job: Any) -> ScoreBreakdown:
    """0–100: can this persona credibly do this job, before writing anything?"""
    description = getattr(job, "description", None)
    required, mentioned = jd_requirements(description)
    role_ratio_only, role_note_only = _role_alignment(persona, getattr(job, "title", None))

    if not required:
        # Either no JD, or a JD that is all company blurb. These are different
        # situations and neither is a zero: a zero sorts to the bottom as if the
        # match were bad, when the truth is we could not read one.
        if not description:
            return ScoreBreakdown(
                score=0,
                keyword_points=0,
                structure_points=0,
                title_points=0,
                notes=["no job description scanned yet — fetch the JD to score it"],
            )
        return ScoreBreakdown(
            score=max(0, min(100, round(60 * role_ratio_only))),
            keyword_points=0,
            structure_points=0,
            title_points=round(60 * role_ratio_only),
            notes=[
                "judged on the title alone — the JD lists no recognisable requirements",
                *( [role_note_only] if role_note_only else [] ),
            ],
        )
    have = persona_terms(persona)
    covered = required & have
    missing = sorted(required - have)
    ratio = len(covered) / len(required)

    role_ratio, role_note = _role_alignment(persona, getattr(job, "title", None))
    keyword_points = round(70 * ratio)
    title_points = round(30 * role_ratio)
    score = keyword_points + title_points

    notes: list[str] = []
    # A posting in another discipline can still list a familiar stack —
    # staffing boards paste one across every req. Coverage alone rated
    # "Power Generation Electrical Engineer" above a Databricks Architect
    # role, so a title from a different field caps the score outright.
    if role_ratio == 0.0:
        score = min(score, 40)
        notes.append(role_note or "different discipline — title shares nothing with this profile")
    elif role_note:
        notes.append(role_note)

    # Two matched terms out of two is not the same evidence as twelve out of
    # twelve, and calling both "strong match" oversells the thin one.
    if len(required) < 3:
        notes.append(
            f"thin evidence — the JD names only {len(required)} recognisable requirement"
            f"{'' if len(required) == 1 else 's'}"
        )

    if ratio >= 0.75 and role_ratio > 0 and len(required) >= 3:
        notes.insert(0, "strong match")
    elif ratio >= 0.5 and role_ratio > 0:
        notes.insert(0, "partial match — resume will have to stretch")
    elif role_ratio > 0:
        notes.insert(0, "weak match — likely not worth an application")
    if missing:
        notes.append(f"not evidenced in history: {', '.join(missing[:8])}")

    return ScoreBreakdown(
        score=max(0, min(100, score)),
        keyword_points=keyword_points,
        structure_points=0,
        title_points=title_points,
        matched=sorted(covered),
        missing=missing,
        notes=notes,
    )
