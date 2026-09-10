from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from openai import AsyncOpenAI

from app.config import get_settings
from app.services.persona import Persona, ground_truth_payload, strip_name_parentheticals
from app.services.openai_compat import chat_completion


class PersonaIdentityError(RuntimeError):
    """A persona lacks identity data required to fill a real application."""


# Must be present on the persona itself. A fallback here would put a different
# person's PII on a live application, so we refuse instead.
REQUIRED_IDENTITY_FIELDS: tuple[str, ...] = (
    "email",
    "phone",
    "location_city",
    "location_state",
)

# Persona-specific but optional. When absent the answer is omitted so the field
# escalates to the LLM/operator rather than being answered with someone else's data.
OPTIONAL_IDENTITY_FIELDS: tuple[str, ...] = (
    "address_line",
    "location_postal",
    "linkedin_url",
    "github_url",
    "citizenship",
)


def split_legal_name(persona: Persona) -> tuple[str, str]:
    """First/last name for form fields. Never guesses, never borrows."""
    ident = persona.identity or {}
    name = strip_name_parentheticals(ident.get("legal_name") or persona.display_name or "")
    parts = [p for p in name.split() if p]
    if len(parts) < 2:
        raise PersonaIdentityError(
            f"{persona.persona_id}: identity.legal_name must be a full name, got {name!r}"
        )
    return parts[0], parts[-1]


def _education_facts(persona: Persona) -> dict[str, str]:
    """School/degree/discipline from this persona's own education ground truth."""
    entries = [e for e in (persona.education_ground_truth or []) if e]
    if not entries:
        return {}
    top = entries[0]
    degree_raw = str(top.get("degree") or "").strip()
    # Personas spell the school key as school / institution / university.
    school = str(
        top.get("school") or top.get("institution") or top.get("university") or ""
    ).strip()
    # Personas write the discipline inside the degree string three ways:
    # "Master of Science (Data Science)", "Bachelor of Science in Computer
    # Science", and "Master's degree, Machine Learning".
    match = (
        re.search(r"\(([^)]+)\)", degree_raw)
        or re.search(r"\bin\s+(.+)$", degree_raw, re.I)
        or re.search(r",\s*(.+)$", degree_raw)
    )
    discipline = (match.group(1) if match else "").strip() or str(
        top.get("field") or top.get("major") or ""
    ).strip()
    facts: dict[str, str] = {}
    if strip_name_parentheticals(degree_raw):
        facts["degree"] = strip_name_parentheticals(degree_raw)
    if discipline:
        facts["discipline"] = discipline
    if school:
        facts["school"] = school
        facts["university"] = school
    return facts


def _current_employer(persona: Persona) -> str:
    """The role still marked Present, else the most recent one listed."""
    roles = [r for r in (persona.experience_ground_truth or []) if r]
    for role in roles:
        if str(role.get("end") or "").strip().lower() in {"present", "current"}:
            return str(role.get("company") or "").strip()
    return str(roles[0].get("company") or "").strip() if roles else ""


def validate_persona_identity(persona: Persona) -> list[str]:
    """Identity problems that would corrupt a submitted application. Empty == safe."""
    problems: list[str] = []
    ident = persona.identity or {}
    for field in REQUIRED_IDENTITY_FIELDS:
        if not str(ident.get(field) or "").strip():
            problems.append(f"identity.{field} is missing")
    try:
        split_legal_name(persona)
    except PersonaIdentityError as exc:
        problems.append(str(exc))
    if not persona.education_ground_truth:
        problems.append("education_ground_truth is empty — school/degree answers unavailable")
    if not persona.experience_ground_truth:
        problems.append("experience_ground_truth is empty — employer answers unavailable")
    return problems


def static_defaults(persona: Persona) -> dict[str, str]:
    """Canonical answers keyed by normalized question fragments.

    Every persona-specific value is read from this persona. Values we do not
    have are omitted rather than defaulted, so the field escalates to the LLM
    or the operator instead of being answered with another persona's data.
    """
    ident = persona.identity or {}
    first, last = split_legal_name(persona)
    today = date.today().isoformat()

    city = str(ident.get("location_city") or "").strip()
    state = str(ident.get("location_state") or "").strip()
    postal = str(ident.get("location_postal") or "").strip()
    street = str(ident.get("address_line") or "").strip()
    address = " ".join(x for x in (street, city, state, postal) if x).strip()

    work_auth = str(ident.get("work_authorization") or "").strip()
    authorized = bool(re.search(r"authoriz|citizen|permanent resident", work_auth, re.I))
    citizenship = str(ident.get("citizenship") or "").strip()
    country = str(ident.get("location_country") or "US").strip()

    defaults: dict[str, str] = {}

    def put(value: Any, *keys: str) -> None:
        """Set keys only when this persona actually has the value."""
        text = str(value or "").strip()
        if not text:
            return
        for key in keys:
            defaults[key] = text

    # --- identity, strictly from this persona ---
    put(first, "first name", "preferred first name")
    put(last, "last name")
    put(ident.get("email"), "email", "email address")
    put(ident.get("phone"), "phone")
    put(city, "city")
    put(state, "state/province", "state")
    put(postal, "postal code", "zip code", "zip")
    put(street, "street address")
    put(address, "full mailing address", "mailing address", "address")
    put(country or "United States", "country of residence", "country")
    put(ident.get("linkedin_url"), "linkedin", "linkedin profile", "linkedin profile url")
    put(ident.get("github_url"), "github", "website")

    # --- claims that must match the attached resume ---
    for key, value in _education_facts(persona).items():
        put(value, key)
    put(_current_employer(persona), "current employer")
    put(
        persona.total_experience,
        "total years",
        "years of relevant experience",
        "years of experience",
    )

    # --- work authorization, derived rather than assumed ---
    if authorized:
        put("Yes", "authorized to work")
        put("No", "sponsorship", "require sponsorship")
    put(citizenship, "immigration status")
    if re.search(r"u\.?s\.? citizen", citizenship, re.I):
        put("Yes", "are you a u.s. citizen", "are you a us citizen", "us citizen", "u.s. citizen")

    if country.upper() in {"US", "USA", "UNITED STATES"}:
        put(
            "Yes",
            "living in the united states",
            "live in the united states",
            "residing in the united states",
            "are you living in the united states",
            "currently living in the",
        )

    put(ident.get("salary_expectation"), "desired salary", "salary")

    # --- EEO: decline rather than assert a demographic we were never told ---
    put(ident.get("gender") or "Decline to self-identify", "gender", "gender identity")
    put(ident.get("ethnicity") or "Decline to self-identify", "racial", "ethnic", "race")
    put(ident.get("disability") or "Decline to self-identify", "disability")
    put(ident.get("veteran_status") or "Decline to self-identify", "veteran", "armed forces")

    # --- genuinely persona-independent process answers ---
    defaults.update(
        {
            "open to travel": "Yes",
            "do you currently work for": "No",
            "previously been employed": "No",
            "previously employed": "No",
            "source": "Other",
            "referred": "No",
            "were you referred": "No",
            "employee referral": "",
            "available start date": today,
            "start date": today,
            "sms": "Yes",
            "consent": "Yes",
            "mobile messages": "Yes",
            "government agency experience": "No",
            "program or government": "No",
            "clearance": "No",
            "security clearance": "No",
            "state bar": "None — not a licensed attorney",
            "bar admission": "None",
            "admitted to": "None",
        }
    )

    # Persona-authored screening answers always win.
    for item in persona.screening_answers or []:
        question = (item.get("question") or "").strip()
        answer = (item.get("answer") or "").strip()
        if question and answer:
            defaults[question.lower()] = answer
    return defaults


def match_static(label: str, defaults: dict[str, str]) -> str | None:
    lab = re.sub(r"[*]+", "", (label or "").lower()).strip()
    lab = re.sub(r"\s+", " ", lab)
    if not lab:
        return None

    # Address "state" must not match "United States" / immigration questions
    def _ok_for_key(key: str) -> bool:
        k = key.strip()
        if k in {"state", "state *", "state/province"} or k == "state":
            if any(
                w in lab
                for w in (
                    "united states",
                    "immigration",
                    "citizen",
                    "sponsorship",
                    "authorization",
                    "living in the",
                    "residing",
                    "state bar",
                    "bar admission",
                    "admitted to",
                    "licensed",
                    "license",
                )
            ):
                return False
            # "state" in "state bar(s)" — not a mailing-state field
            if "bar" in lab or "attorney" in lab or "lawyer" in lab:
                return False
        if k in {"country", "country of residence"}:
            if "immigration" in lab or ("status" in lab and "united" in lab):
                return False
        return True

    # Longest keys first so "linkedin profile url" beats "linkedin"
    for key in sorted(defaults.keys(), key=len, reverse=True):
        if not _ok_for_key(key):
            continue
        if len(key) <= 5:
            # Word-ish boundaries — prevents "state" matching inside "states"
            if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", lab):
                return defaults[key]
        elif key in lab:
            return defaults[key]
        # Only allow lab⊂key when the label is a real phrase (not "state" ⊂ "...united states...")
        elif len(lab) >= 12 and lab in key:
            return defaults[key]

    # Bare address-state field: short label that is essentially "State"
    if re.fullmatch(r"state(\s*/\s*province)?", lab) or lab in {"state", "province"}:
        return defaults.get("state/province") or defaults.get("state")
    return None


def pick_option(answer: str, options: list[str]) -> str:
    if not options:
        return answer
    cleaned = [
        o
        for o in options
        if o
        and o.strip()
        and o.strip().lower()
        not in {"select...", "select", "—", "-", "please select", "choose one"}
    ]
    if not cleaned:
        cleaned = list(options)
    if not answer:
        return cleaned[0]

    a = answer.lower().strip()
    for o in cleaned:
        if o.lower().strip() == a:
            return o

    for o in cleaned:
        ol = o.lower()
        if a in ol or ol in a:
            return o

    if a in {"yes", "y", "true"}:
        for o in cleaned:
            ol = o.lower().strip()
            if ol.startswith("yes") or ol == "y":
                return o
    if a in {"no", "n", "false"}:
        for o in cleaned:
            ol = o.lower().strip()
            if ol.startswith("no") or ol == "n":
                return o

    if any(x in a for x in ("citizen", "us citizen", "u.s. citizen", "authorized")):
        for prefer in (
            "us citizen",
            "u.s. citizen",
            "citizen of the united states",
            "united states citizen",
            "authorized to work",
            "permanent resident",
            "green card",
        ):
            for o in cleaned:
                if prefer in o.lower():
                    return o
        for o in cleaned:
            if "citizen" in o.lower() or "authorized" in o.lower():
                return o

    tokens = [t for t in re.split(r"[^a-z0-9]+", a) if len(t) > 2]
    best = None
    best_score = 0
    for o in cleaned:
        ol = o.lower()
        score = sum(1 for t in tokens if t in ol)
        if score > best_score:
            best_score = score
            best = o
    return best or cleaned[0]


_SKIP_LABEL_FRAGMENTS = (
    "g-recaptcha",
    "recaptcha",
    "resumator-resumetext",
    "honeypot",
    "website_url",
)


def is_phone_country_code_widget(field: dict[str, Any]) -> bool:
    """Ashby/Greenhouse-style +1 prefix control next to the real phone input."""
    label = (field.get("label") or "").lower().strip()
    kind = (field.get("kind") or "").lower()
    tag = (field.get("tag") or "").lower()
    value = (field.get("value") or "").strip()
    ftype = (field.get("type") or "").lower()
    if label in {"+1", "select...", "search"} and kind == "combobox":
        return True
    if "phone" not in label:
        return False
    if tag == "input" or ftype in {"tel", "text", "email"}:
        if field.get("name") or field.get("id"):
            return False
    if kind == "combobox" or tag in {"div", "button", "span"}:
        if re.fullmatch(r"\+?\d{1,3}", value) or value in {"+1", "1", "US", "USA"}:
            return True
        if not (field.get("name") or field.get("id") or field.get("selector_hint")):
            return True
    return False


# Combobox widgets whose "label" is actually the current selected value, not the question
_COMBO_VALUE_LABELS = {
    "select...",
    "select",
    "search",
    "+1",
    "choose",
    "please select",
}


def is_junk_combobox_label(field: dict[str, Any]) -> bool:
    kind = (field.get("kind") or "").lower()
    if kind != "combobox":
        return False
    label = re.sub(r"\*+", "", (field.get("label") or "")).strip().lower()
    value = (field.get("value") or "").strip().lower()
    if label in _COMBO_VALUE_LABELS:
        return True
    # Label equals current value and has no id — orphan option-as-label widget
    if label and value and label == value and not (field.get("id") or field.get("name")):
        # Long EE option texts used as labels
        if len(label) > 25 or label.startswith(("i am ", "no, i ", "yes,", "prefer ")):
            return True
        if any(
            x in label
            for x in (
                "bachelor",
                "master",
                "degree",
                "protected veteran",
                "disability",
                "citizen",
            )
        ):
            return True
    return False


def looks_like_dropdown_field(field: dict[str, Any]) -> bool:
    """Greenhouse/etc.: text inputs that are really combobox selects."""
    kind = (field.get("kind") or "").lower()
    if kind in {"select", "combobox"}:
        return True
    fid = (field.get("id") or "").lower()
    label = (field.get("label") or "").lower()
    dropdown_ids = {
        "gender",
        "country",
        "race",
        "veteran_status",
        "disability_status",
        "hispanic_ethnicity",
        "pronouns",
    }
    if fid in dropdown_ids:
        return True
    if fid.startswith("school") or fid.startswith("degree") or "discipline" in fid:
        return True
    short_label_keys = (
        "gender",
        "country",
        "race",
        "ethnicity",
        "veteran",
        "disability",
        "school",
        "degree",
        "transgender",
        "hispanic",
        "pronoun",
        "source",
        "how did you hear",
    )
    if len(label) < 90 and any(k in label for k in short_label_keys):
        # Free-text essay questions are long; skip those
        if "describe" in label and len(label) > 60 and "gender" not in label and "racial" not in label:
            return False
        return True
    # Long EE / compliance dropdowns (Greenhouse custom questions)
    if any(
        k in label
        for k in (
            "veteran",
            "disability",
            "gender identity",
            "racial/ethnic",
            "ethnic background",
            "protected veteran",
            "united states armed",
        )
    ):
        return True
    return False


def should_skip_field(field: dict[str, Any]) -> bool:
    label = (field.get("label") or "").lower()
    name = (field.get("name") or "").lower()
    ftype = (field.get("type") or "").lower()
    if ftype == "search" or label == "search":
        return True
    if is_phone_country_code_widget(field):
        return True
    if is_junk_combobox_label(field):
        return True
    blob = f"{label} {name}"
    return any(s in blob for s in _SKIP_LABEL_FRAGMENTS)


def phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def normalize_phone_for_label(label: str, phone: str) -> str:
    """If UI already shows +1, type national digits only; else Ashby-friendly 1-NXX-NXX-XXXX."""
    raw = (phone or "").strip()
    if not raw:
        return raw
    digits = phone_digits(raw)
    national = digits
    if digits.startswith("1") and len(digits) == 11:
        national = digits[1:]
    elif len(digits) == 10:
        national = digits

    lab = re.sub(r"\*+", "", (label or "").lower())
    # Label includes country code prefix ("Phone +1", "Mobile (+1)", …)
    prefix_in_label = bool(
        re.search(r"phone\s*\+?\s*1\b", lab)
        or re.search(r"\+1\b", lab)
        or "country code" in lab
    )
    if prefix_in_label and len(national) == 10:
        return f"({national[:3]}) {national[3:6]}-{national[6:]}"
    if prefix_in_label:
        return national or raw
    # Ashby / many ATS placeholders look like 1-415-555-1234
    if len(national) == 10:
        return f"1-{national[:3]}-{national[3:6]}-{national[6:]}"
    return raw


def dedupe_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one field per kind+label; prefer named/id'd controls over ghosts."""
    best: dict[str, tuple[int, dict[str, Any]]] = {}
    order: list[str] = []

    def _score(f: dict[str, Any]) -> int:
        score = 0
        if f.get("name"):
            score += 4
        if f.get("id"):
            score += 3
        if f.get("selector_hint"):
            score += 1
        tag = (f.get("tag") or "").lower()
        ftype = (f.get("type") or "").lower()
        kind = (f.get("kind") or "").lower()
        if tag in {"input", "select"}:
            score += 2
        elif tag == "textarea":
            score -= 1
        if ftype == "tel":
            score += 3
        if kind == "combobox" and "phone" in (f.get("label") or "").lower():
            score -= 5
        if (f.get("kind") or "") == "select" and (f.get("options") or []):
            score += 2
        return score

    for f in fields:
        if should_skip_field(f):
            continue
        label = re.sub(r"\*+", "", (f.get("label") or "").lower()).strip()
        label = re.sub(r"\s+", " ", label)[:120]
        kind = (f.get("kind") or f.get("type") or "").lower()
        # Collapse phone text+combobox ghosts onto one soft key
        soft_kind = kind
        if "phone" in label and kind in {"text", "tel", "combobox"}:
            soft_kind = "phone"
        soft = f"{soft_kind}|{label}" if label else f"{kind}|{f.get('name')}|{f.get('id')}|{id(f)}"
        sc = _score(f)
        if soft not in best:
            best[soft] = (sc, f)
            order.append(soft)
        elif sc > best[soft][0]:
            best[soft] = (sc, f)
    return [best[k][1] for k in order]


async def generate_field_answers(
    *,
    persona: Persona,
    fields: list[dict[str, Any]],
    job_meta: dict[str, Any],
    resume_excerpt: str = "",
    jd_excerpt: str = "",
) -> dict[str, str]:
    """Return map label -> answer string for each field."""
    defaults = static_defaults(persona)
    answers: dict[str, str] = {}
    unresolved: list[dict[str, Any]] = []
    fields = dedupe_fields(fields)

    for f in fields:
        label = (f.get("label") or "").strip()
        if not label:
            continue
        if should_skip_field(f):
            continue
        if (f.get("kind") or f.get("type") or "").lower() == "file":
            continue
        hit = match_static(label, defaults)
        if hit is not None:
            opts = f.get("options") or []
            answers[label] = pick_option(hit, opts) if opts else hit
        else:
            unresolved.append(f)

    if unresolved:
        gpt = await _gpt_answer_fields(
            persona=persona,
            fields=unresolved,
            defaults=defaults,
            job_meta=job_meta,
            resume_excerpt=resume_excerpt,
            jd_excerpt=jd_excerpt,
        )
        for label, ans in gpt.items():
            if not label:
                continue
            opts: list[str] = []
            for f in unresolved:
                if (f.get("label") or "").strip() == label:
                    opts = f.get("options") or []
                    break
            answers[label] = pick_option(str(ans), opts) if opts else str(ans)

        for f in unresolved:
            label = (f.get("label") or "").strip()
            if not label or label in answers:
                continue
            opts = f.get("options") or []
            lab = label.lower()
            if not opts:
                continue
            if "immigration" in lab or "citizen" in lab or "authorization" in lab:
                answers[label] = pick_option(
                    defaults.get("immigration status") or "US Citizen", opts
                )
            elif "living in the united states" in lab or "live in the united states" in lab:
                answers[label] = pick_option("Yes", opts)
            elif any(w in lab for w in ("sponsor", "sponsorship")):
                answers[label] = pick_option("No", opts)

    return answers


async def _gpt_answer_fields(
    *,
    persona: Persona,
    fields: list[dict[str, Any]],
    defaults: dict[str, str],
    job_meta: dict[str, Any],
    resume_excerpt: str,
    jd_excerpt: str,
) -> dict[str, str]:
    settings = get_settings()
    if not settings.openai_api_key:
        return {}

    payload_fields = [
        {
            "label": f.get("label"),
            "kind": f.get("kind"),
            "required": f.get("required"),
            "options": (f.get("options") or [])[:40],
        }
        for f in fields
    ]

    display = persona.display_name or persona.persona_id
    system = (
        f"You fill ATS job-application forms truthfully for candidate {display}. "
        "Use ONLY provided ground truth / defaults. Never invent employers, degrees, "
        "clearances, or metrics. If a dropdown has options, answer MUST be exactly one "
        "of those option strings (copy verbatim). "
        "Prefer immigration/work-auth answers from ground truth / defaults. "
        "Never answer location abbreviations (TX, NY) for immigration or residency questions. "
        "When answering role/experience questions, ground answers in the resume excerpt and JD. "
        "Return STRICT JSON object mapping field label -> answer string. No markdown."
    )
    user = {
        "identity_ground_truth": ground_truth_payload(persona),
        "static_defaults": defaults,
        "job": {
            "company": job_meta.get("company"),
            "title": job_meta.get("title"),
            "location": job_meta.get("location"),
            "url": job_meta.get("url"),
        },
        "jd_excerpt": (jd_excerpt or "")[:8000],
        "resume_excerpt": (resume_excerpt or "")[:6000],
        "fields_to_answer": payload_fields,
        "rules": [
            "Prefer ground-truth / static_defaults over invention",
            "US work authorization and sponsorship answers must follow identity defaults",
            "Start date = today when asked (can start immediately)",
            "Demographic answers follow identity defaults when present",
            "No employee referral unless ground truth says otherwise",
            "For company-specific 'do you work for X' answer No unless currently employed there",
            "Source: Other / Job Board / Company website — pick closest option",
            "Experience / technical answers should align with resume_excerpt and JD",
        ],
    }

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await chat_completion(
        client,
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        temperature=0.2,
    )
    raw = (resp.choices[0].message.content or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.M)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if v is not None}
    except json.JSONDecodeError:
        pass
    return {}
