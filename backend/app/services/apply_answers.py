from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from openai import AsyncOpenAI

from app.config import get_settings
from app.services.persona import Persona, ground_truth_payload


def static_defaults(persona: Persona) -> dict[str, str]:
    """Canonical answers keyed by normalized question fragments."""
    ident = persona.identity or {}
    name = ident.get("legal_name") or persona.display_name
    parts = name.split()
    first = parts[0] if parts else "Phong"
    last = parts[-1] if len(parts) > 1 else "Cao"
    address = " ".join(
        x
        for x in [
            ident.get("address_line"),
            ident.get("location_city"),
            ident.get("location_state"),
            ident.get("location_postal"),
        ]
        if x
    ).strip() or "21717 Gallus Dr Pflugerville, TX 78660"
    today = date.today().isoformat()
    citizen = ident.get("citizenship") or "US Citizen"
    work_auth = ident.get("work_authorization") or "Authorized to work in the US"

    from_persona: dict[str, str] = {}
    for item in persona.screening_answers or []:
        q = (item.get("question") or "").strip()
        a = (item.get("answer") or "").strip()
        if q and a:
            from_persona[q] = a

    defaults = {
        "first name": first,
        "last name": last,
        "preferred first name": first,
        "email": ident.get("email") or "",
        "email address": ident.get("email") or "",
        "phone": ident.get("phone") or "",
        "country of residence": ident.get("location_country") or "United States",
        "country": ident.get("location_country") or "United States",
        "city": ident.get("location_city") or "Pflugerville",
        # Prefer longer keys; bare "state" is matched only via word-boundary + guard
        "state/province": ident.get("location_state") or "TX",
        "postal code": ident.get("location_postal") or "78660",
        "zip code": ident.get("location_postal") or "78660",
        "zip": ident.get("location_postal") or "78660",
        "state": ident.get("location_state") or "TX",
        "full mailing address": address,
        "mailing address": address,
        "street address": ident.get("address_line") or "21717 Gallus Dr",
        "address": address,
        "linkedin": ident.get("linkedin_url") or "https://www.linkedin.com/in/phcao/",
        "linkedin profile": ident.get("linkedin_url") or "https://www.linkedin.com/in/phcao/",
        "linkedin profile url": ident.get("linkedin_url") or "https://www.linkedin.com/in/phcao/",
        "github": ident.get("github_url") or "https://github.com/phongvcao",
        "website": ident.get("github_url") or "https://github.com/phongvcao",
        "degree": "Bachelor's Degree",
        "discipline": "Computer Science",
        "school": "Stony Brook University",
        "university": "Stony Brook University",
        "are you a u.s. citizen": "Yes",
        "are you a us citizen": "Yes",
        "us citizen": "Yes",
        "u.s. citizen": "Yes",
        "citizenship": citizen,
        "immigration status": citizen,
        "current immigration status": citizen,
        "work authorization": work_auth,
        "authorized to work": "Yes",
        "living in the united states": "Yes",
        "live in the united states": "Yes",
        "residing in the united states": "Yes",
        "are you living in the united states": "Yes",
        "currently living in the": "Yes",
        "sponsorship": "No",
        "require sponsorship": "No",
        "total years": "11",
        "years of relevant experience": "11",
        "years of experience": "11",
        "current employer": "NVIDIA",
        "current job title": ident.get("default_professional_title")
        or persona.default_professional_title
        or "Senior Software Engineer",
        "desired salary": ident.get("salary_expectation") or "150000-180000 USD per year",
        "salary": ident.get("salary_expectation") or "150000",
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
        "gender": ident.get("gender") or "Male",
        "gender identity": ident.get("gender") or "Male",
        "racial": ident.get("ethnicity") or "Asian",
        "ethnic": ident.get("ethnicity") or "Asian",
        "race": ident.get("ethnicity") or "Asian",
        "disability": "No, I do not have a disability",
        "veteran": "I am not a protected veteran",
        "armed forces": "No",
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
    for q, a in from_persona.items():
        defaults[q.lower()] = a
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
        return defaults.get("state/province") or defaults.get("state") or "TX"
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
    resp = await client.chat.completions.create(
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
