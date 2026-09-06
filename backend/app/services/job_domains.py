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


# Concrete job titles inside each domain.
#
# A domain says what field a job is in; a role says which job. "Data
# Engineering" matches 446 listings in the current table, but the operator
# usually wants a slice of that — Senior Data Engineer, Staff, Principal — and
# selecting the domain alone cannot express it.
#
# Ordered seniority-first within a domain, because that is how the list reads.
JOB_ROLES: dict[str, list[dict[str, Any]]] = {
    "de": [
        {"id": "de_data_engineer", "label": "Data Engineer",
         "patterns": [r"\bdata engineer\b"]},
        {"id": "de_senior", "label": "Senior Data Engineer",
         "patterns": [r"\b(?:senior|sr\.?)\s+data engineer\b"]},
        {"id": "de_staff", "label": "Staff Data Engineer",
         "patterns": [r"\bstaff\s+data engineer\b"]},
        {"id": "de_principal", "label": "Principal Data Engineer",
         "patterns": [r"\bprincipal\s+data engineer\b"]},
        {"id": "de_lead", "label": "Lead Data Engineer",
         "patterns": [r"\b(?:lead|leading)\s+data engineer\b", r"\bdata engineering lead\b"]},
        {"id": "de_cloud", "label": "AWS / Azure / GCP Data Engineer",
         "patterns": [r"\b(?:aws|azure|gcp|google cloud)\s+data engineer\b"]},
        {"id": "de_bigdata", "label": "Big Data / Spark Engineer",
         "patterns": [r"\bbig ?data engineer\b", r"\bspark engineer\b", r"\bhadoop engineer\b"]},
        {"id": "de_etl", "label": "ETL / Pipeline Engineer",
         "patterns": [r"\betl developer\b", r"\betl engineer\b", r"\bpipeline engineer\b"]},
        {"id": "de_platform", "label": "Data Platform / Infrastructure",
         "patterns": [r"\bdata platform engineer\b", r"\bdata infrastructure engineer\b"]},
        {"id": "de_analytics", "label": "Analytics Engineer",
         "patterns": [r"\banalytics engineer\b"]},
        {"id": "de_architect", "label": "Data Architect",
         "patterns": [r"\bdata architect\b", r"\bdata engineering architect\b"]},
        {"id": "de_warehouse", "label": "Data Warehouse Engineer",
         "patterns": [r"\bdata warehouse (?:engineer|developer)\b", r"\bsnowflake engineer\b"]},
    ],
    "ai": [
        {"id": "ai_ml_engineer", "label": "Machine Learning Engineer",
         "patterns": [r"\b(?:machine learning|ml)\s+engineer\b"]},
        {"id": "ai_senior_ml", "label": "Senior ML Engineer",
         "patterns": [r"\b(?:senior|sr\.?|staff|principal)\s+(?:machine learning|ml)\s+engineer\b"]},
        {"id": "ai_ai_engineer", "label": "AI Engineer",
         "patterns": [r"\bai engineer\b", r"\bai/ml engineer\b"]},
        {"id": "ai_llm", "label": "LLM / GenAI Engineer",
         "patterns": [r"\bllm\b", r"\bgenerative ai\b", r"\bgenai\b", r"\bprompt engineer\b"]},
        {"id": "ai_mlops", "label": "MLOps Engineer",
         "patterns": [r"\bml ?ops engineer\b", r"\bml platform engineer\b"]},
        {"id": "ai_research", "label": "Research / Applied Scientist",
         "patterns": [r"\bresearch (?:engineer|scientist)\b", r"\bapplied scientist\b"]},
        {"id": "ai_nlp_cv", "label": "NLP / Computer Vision",
         "patterns": [r"\bnlp engineer\b", r"\bcomputer vision engineer\b"]},
    ],
    "ds": [
        {"id": "ds_data_scientist", "label": "Data Scientist",
         "patterns": [r"\bdata scientist\b"]},
        {"id": "ds_senior", "label": "Senior Data Scientist",
         "patterns": [r"\b(?:senior|sr\.?|staff|principal|lead)\s+data scientist\b"]},
        {"id": "ds_applied", "label": "Applied / Decision Scientist",
         "patterns": [r"\bapplied scientist\b", r"\bdecision scientist\b"]},
        {"id": "ds_quant", "label": "Quantitative Analyst",
         "patterns": [r"\bquantitative (?:analyst|researcher)\b", r"\bquant analyst\b"]},
    ],
    "da": [
        {"id": "da_data_analyst", "label": "Data Analyst",
         "patterns": [r"\bdata analyst\b"]},
        {"id": "da_senior", "label": "Senior Data Analyst",
         "patterns": [r"\b(?:senior|sr\.?|lead)\s+data analyst\b"]},
        {"id": "da_bi", "label": "BI Analyst / Developer",
         "patterns": [r"\bbi (?:analyst|developer|engineer)\b",
                      r"\bbusiness intelligence (?:analyst|developer|engineer)\b"]},
        {"id": "da_reporting", "label": "Reporting / Tableau / Power BI",
         "patterns": [r"\btableau\b", r"\bpower bi\b", r"\breporting analyst\b"]},
        {"id": "da_business", "label": "Business / Product Analyst",
         "patterns": [r"\bbusiness analyst\b", r"\bproduct analyst\b"]},
    ],
    "sf": [
        {"id": "sf_developer", "label": "Salesforce Developer",
         "patterns": [r"\bsalesforce developer\b", r"\bapex developer\b"]},
        {"id": "sf_admin", "label": "Salesforce Administrator",
         "patterns": [r"\bsalesforce admin(?:istrator)?\b"]},
        {"id": "sf_architect", "label": "Salesforce Architect",
         "patterns": [r"\bsalesforce (?:technical )?architect\b"]},
        {"id": "sf_consultant", "label": "Salesforce Consultant / BA",
         "patterns": [r"\bsalesforce (?:consultant|business analyst)\b"]},
        {"id": "sf_cpq", "label": "CPQ / Marketing Cloud",
         "patterns": [r"\bcpq\b", r"\bmarketing cloud\b", r"\bservice cloud\b", r"\bsales cloud\b"]},
    ],
    "dv": [
        {"id": "dv_devops", "label": "DevOps Engineer",
         "patterns": [r"\bdevops engineer\b"]},
        {"id": "dv_sre", "label": "Site Reliability Engineer",
         "patterns": [r"\bsite reliability engineer\b", r"\bsre\b"]},
        {"id": "dv_platform", "label": "Platform Engineer",
         "patterns": [r"\bplatform engineer\b"]},
        {"id": "dv_infra", "label": "Infrastructure Engineer",
         "patterns": [r"\binfrastructure engineer\b"]},
        {"id": "dv_senior", "label": "Senior / Staff DevOps",
         "patterns": [r"\b(?:senior|sr\.?|staff|principal|lead)\s+(?:devops|sre|platform) engineer\b"]},
    ],
    "ce": [
        {"id": "ce_cloud", "label": "Cloud Engineer",
         "patterns": [r"\bcloud engineer\b"]},
        {"id": "ce_architect", "label": "Cloud Architect",
         "patterns": [r"\bcloud (?:solutions )?architect\b"]},
        {"id": "ce_aws", "label": "AWS Engineer",
         "patterns": [r"\baws (?:engineer|cloud engineer|developer)\b"]},
        {"id": "ce_azure", "label": "Azure Engineer",
         "patterns": [r"\bazure (?:engineer|cloud engineer|developer)\b"]},
        {"id": "ce_gcp", "label": "GCP Engineer",
         "patterns": [r"\b(?:gcp|google cloud) (?:engineer|developer)\b"]},
    ],
    "be": [
        {"id": "be_backend", "label": "Backend Engineer",
         "patterns": [r"\bback ?end (?:engineer|developer)\b"]},
        {"id": "be_senior", "label": "Senior / Staff Backend",
         "patterns": [r"\b(?:senior|sr\.?|staff|principal|lead)\s+back ?end\b"]},
        {"id": "be_python", "label": "Python Engineer",
         "patterns": [r"\bpython (?:engineer|developer)\b"]},
        {"id": "be_java", "label": "Java Engineer",
         "patterns": [r"\bjava (?:engineer|developer)\b"]},
        {"id": "be_api", "label": "API / Microservices",
         "patterns": [r"\bapi (?:engineer|developer)\b", r"\bmicroservices? (?:engineer|developer)\b"]},
    ],
    "fs": [
        {"id": "fs_full_stack", "label": "Full Stack Engineer",
         "patterns": [r"\bfull ?stack (?:engineer|developer)\b"]},
        {"id": "fs_senior", "label": "Senior / Staff Full Stack",
         "patterns": [r"\b(?:senior|sr\.?|staff|principal|lead)\s+full ?stack\b"]},
    ],
    "fe": [
        {"id": "fe_frontend", "label": "Frontend Engineer",
         "patterns": [r"\bfront ?end (?:engineer|developer)\b"]},
        {"id": "fe_react", "label": "React / Angular / Vue",
         "patterns": [r"\breact (?:engineer|developer)\b", r"\bangular (?:engineer|developer)\b",
                      r"\bvue (?:engineer|developer)\b"]},
        {"id": "fe_ui", "label": "UI Engineer",
         "patterns": [r"\bui (?:engineer|developer)\b", r"\bweb developer\b"]},
    ],
    "se": [
        {"id": "se_software", "label": "Software Engineer",
         "patterns": [r"\bsoftware (?:engineer|developer)\b", r"\bsde\b"]},
        {"id": "se_senior", "label": "Senior Software Engineer",
         "patterns": [r"\b(?:senior|sr\.?)\s+software engineer\b"]},
        {"id": "se_staff", "label": "Staff / Principal Engineer",
         "patterns": [r"\b(?:staff|principal|distinguished)\s+(?:software )?engineer\b"]},
        {"id": "se_lead", "label": "Lead / Engineering Manager",
         "patterns": [r"\bengineering manager\b", r"\btech(?:nical)? lead\b"]},
    ],
    "sq": [
        {"id": "sq_sql_dev", "label": "SQL Developer",
         "patterns": [r"\bsql (?:developer|engineer)\b"]},
        {"id": "sq_dba", "label": "Database Administrator",
         "patterns": [r"\bdatabase administrator\b", r"\bdba\b"]},
        {"id": "sq_ssis", "label": "SSIS / SSRS / MSBI",
         "patterns": [r"\bssis\b", r"\bssrs\b", r"\bmsbi\b"]},
        {"id": "sq_db_dev", "label": "Database Developer",
         "patterns": [r"\bdatabase developer\b", r"\bpl/?sql developer\b"]},
    ],
}

_ROLE_COMPILED: dict[str, list[tuple[str, list[Any]]]] = {
    domain: [(r["id"], [re.compile(p, re.I) for p in r["patterns"]]) for r in roles]
    for domain, roles in JOB_ROLES.items()
}

_ROLE_LABELS: dict[str, str] = {
    role["id"]: role["label"] for roles in JOB_ROLES.values() for role in roles
}


def roles_for_domain(domain_id: str) -> list[dict[str, Any]]:
    """The concrete titles offered under one domain."""
    return [
        {"id": r["id"], "label": r["label"]} for r in JOB_ROLES.get(domain_id, [])
    ]


def role_label(role_id: str) -> str:
    return _ROLE_LABELS.get(role_id, role_id)


def classify_roles(title: str | None) -> list[str]:
    """Role ids a job title matches, across every domain."""
    text = (title or "").lower()
    if not text:
        return []
    found: list[str] = []
    for roles in _ROLE_COMPILED.values():
        for role_id, patterns in roles:
            if any(p.search(text) for p in patterns):
                found.append(role_id)
    return found


def title_matches_roles(title: str | None, role_ids: list[str] | None) -> bool:
    """True when no roles are selected, or the title matches one of them."""
    if not role_ids:
        return True
    wanted = set(role_ids)
    return bool(wanted & set(classify_roles(title)))


# --- Search queries ---------------------------------------------------------
# A label reads well in the picker; it is not always a job title someone would
# search for. "AWS / Azure / GCP Data Engineer" names one row in the UI but
# three separate searches on a board. These are the strings to actually send.
#
# Only the compound labels need an entry — everything else searches for the
# label as written.
ROLE_QUERIES: dict[str, list[str]] = {
    "de_cloud": ["AWS Data Engineer", "Azure Data Engineer", "GCP Data Engineer"],
    "de_bigdata": ["Big Data Engineer", "Spark Engineer"],
    "de_etl": ["ETL Developer", "Data Pipeline Engineer"],
    "de_platform": ["Data Platform Engineer", "Data Infrastructure Engineer"],
    "ai_llm": ["LLM Engineer", "Generative AI Engineer"],
    "ai_research": ["Research Scientist", "Applied Scientist"],
    "ai_nlp_cv": ["NLP Engineer", "Computer Vision Engineer"],
    "ds_applied": ["Applied Scientist", "Decision Scientist"],
    "da_bi": ["BI Analyst", "BI Developer"],
    "da_reporting": ["Tableau Developer", "Power BI Developer"],
    "da_business": ["Business Analyst", "Product Analyst"],
    "sf_consultant": ["Salesforce Consultant", "Salesforce Business Analyst"],
    "sf_cpq": ["Salesforce CPQ Developer", "Marketing Cloud Developer"],
    "dv_senior": ["Senior DevOps Engineer", "Staff DevOps Engineer"],
    "be_senior": ["Senior Backend Engineer", "Staff Backend Engineer"],
    "be_api": ["API Engineer", "Microservices Engineer"],
    "fs_senior": ["Senior Full Stack Engineer", "Staff Full Stack Engineer"],
    "fe_react": ["React Developer", "Angular Developer", "Vue Developer"],
    "se_staff": ["Staff Software Engineer", "Principal Software Engineer"],
    "se_lead": ["Lead Software Engineer", "Engineering Manager"],
    "sq_ssis": ["SSIS Developer", "SSRS Developer", "MSBI Developer"],
}


def role_queries(role_id: str) -> list[str]:
    """Search strings for a role — its label, unless it names several titles."""
    explicit = ROLE_QUERIES.get(role_id)
    if explicit:
        return list(explicit)
    label = role_label(role_id)
    return [label] if label and label != role_id else []


def domain_queries(domain_id: str) -> list[str]:
    """Every search string for a domain, in picker order and deduplicated."""
    out: list[str] = []
    for role in roles_for_domain(domain_id):
        for query in role_queries(role["id"]):
            if query not in out:
                out.append(query)
    return out
