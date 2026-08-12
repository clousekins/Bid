from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from app.config import get_settings


@dataclass
class GateResult:
    ok: bool
    reason: str = ""


def _load_rules() -> dict:
    path = get_settings().config_dir / "remote_us_rules.yml"
    if not path.exists():
        return {"mode": "us_remote_strict"}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


REMOTE_RE = re.compile(
    r"\b(remote|work\s*from\s*home|wfh|distributed|anywhere|"
    r"remote\s*solely|remote\s*ok|remote\s*only|fully\s*remote)\b",
    re.I,
)
HYBRID_RE = re.compile(
    r"\b(hybrid|onsite|on-site|in[\s-]office|office[\s-]based|in[\s-]person\s+only)\b",
    re.I,
)

# Explicit US eligibility — never match English pronoun "us"
US_SIGNAL_RE = re.compile(
    r"(?:"
    r"\bunited\s+states\b|"
    r"\bu\.s\.a\.?\b|"
    r"\bu\.s\.\b|"
    r"\busa\b|"
    r"\bus[\s,/|-]*only\b|"
    r"\busa[\s,/|-]*only\b|"
    r"\bremote[\s,/|-]*us\b|"
    r"\bremote[\s,/|-]*usa\b|"
    r"\bremote[\s,/|-]*united\s+states\b|"
    r"\banywhere\s+in\s+the\s+(?:us|usa|u\.s\.a?\.?|united\s+states)\b|"
    r"\bbased\s+in\s+the\s+(?:us|usa|u\.s\.a?\.?|united\s+states)\b|"
    r"\bmust\s+be\s+(?:located\s+)?in\s+the\s+(?:us|usa|united\s+states)\b|"
    r"\b(?:work|working)\s+(?:from|in)\s+the\s+(?:us|usa|united\s+states)\b"
    r")",
    re.I,
)
US_CAPS_RE = re.compile(r"(?<![A-Za-z])US(?![A-Za-z])")

US_STATE_NAME_RE = re.compile(
    r"\b("
    r"alabama|alaska|arizona|arkansas|"
    r"(?<!baja\s)california|"
    r"colorado|connecticut|delaware|florida|"
    # omit "georgia" — conflicts with the country; use GA / United States instead
    r"hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|"
    r"louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|"
    r"missouri|montana|nebraska|nevada|new\s+hampshire|new\s+jersey|new\s+mexico|"
    r"new\s+york|north\s+carolina|north\s+dakota|ohio|oklahoma|oregon|"
    r"pennsylvania|rhode\s+island|south\s+carolina|south\s+dakota|tennessee|"
    r"texas|utah|vermont|virginia|washington|west\s+virginia|wisconsin|wyoming|"
    r"district\s+of\s+columbia|washington\s*,?\s*d\.?c\.?"
    r")\b",
    re.I,
)# Case-sensitive state/DC abbreviations (avoid matching English "in", "or", "me")
US_STATE_ABBR_RE = re.compile(
    r"(?<![A-Za-z])("
    r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|"
    r"MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|"
    r"DC"
    r")(?![A-Za-z])"
)

NON_US_GEO_RE = re.compile(
    r"\b("
    r"spain|portugal|france|germany|italy|netherlands|belgium|sweden|norway|"
    r"denmark|finland|poland|romania|ukraine|ireland|scotland|wales|england|"
    r"united\s+kingdom|\buk\b|great\s+britain|"
    r"india|pakistan|bangladesh|singapore|philippines|indonesia|vietnam|"
    r"thailand|japan|china|south\s*korea|korea|taiwan|hong\s+kong|"
    r"australia|new\s+zealand|"
    r"brazil|argentina|mexico|chile|colombia|peru|uruguay|latam|"
    r"latin\s+america|costa\s+rica|guatemala|nicaragua|panama|honduras|"
    r"canada|toronto|vancouver|montreal|ontario|quebec|british\s+columbia|"
    r"emea|apac|europe|european\s+union|\beu\b|"
    r"israel|uae|dubai|saudi\s+arabia|south\s+africa|nigeria|kenya|"
    r"switzerland|austria|czech(?:ia)?|hungary|greece|turkey|egypt|"
    r"serbia|slovenia|croatia|slovakia|bulgaria|estonia|latvia|lithuania|"
    r"cyprus|armenia|moldova|north\s+macedonia|macedonia|sri\s+lanka|"
    r"russia|belarus|georgia(?!\s*,\s*united\s+states)|baja\s+california"
    r")\b",
    re.I,
)
NON_US_ONLY_RE = re.compile(
    r"\b(canada only|uk only|europe only|eu only|india only|apac only|"
    r"emea only|latam only|brazil only|spain only|mexico only)\b",
    re.I,
)


def _has_us_signal(text: str) -> bool:
    if not text:
        return False
    return bool(
        US_SIGNAL_RE.search(text)
        or US_CAPS_RE.search(text)
        or US_STATE_NAME_RE.search(text)
        or US_STATE_ABBR_RE.search(text)
    )


def _has_non_us_geo(text: str) -> bool:
    return bool(text and NON_US_GEO_RE.search(text))


def check_remote_us(
    location: str | None,
    description: str | None = None,
    *,
    work_arrangement: str | None = None,
    remote_restricted_to: str | None = None,
) -> GateResult:
    """Strict remote-US gate.

    Location / remote_restricted_to win over JD body copy. Incidental
    "United States" in a description must not salvage Spain/India/Brazil roles.
    """
    rules = _load_rules()
    mode = rules.get("mode", "us_remote_strict")
    if mode != "us_remote_strict":
        return GateResult(ok=True)

    loc = (location or "").strip()
    wa = (work_arrangement or "").strip()
    rr = (remote_restricted_to or "").strip()
    desc = description or ""
    place_blob = f"{loc}\n{rr}".strip()
    signal_blob = f"{loc}\n{wa}\n{rr}".strip()
    text = f"{signal_blob}\n{desc}"

    if NON_US_ONLY_RE.search(text):
        return GateResult(ok=False, reason="non_us_only")

    # Hard reject: location / restriction names a non-US country/region
    # (unless the same field also clearly scopes to US, e.g. rare dual listings)
    if place_blob and _has_non_us_geo(place_blob) and not _has_us_signal(place_blob):
        return GateResult(ok=False, reason="not_us")

    if wa and HYBRID_RE.search(wa) and not REMOTE_RE.search(wa):
        return GateResult(ok=False, reason="hybrid_or_onsite")

    if HYBRID_RE.search(loc) and not REMOTE_RE.search(wa) and not REMOTE_RE.search(loc):
        return GateResult(ok=False, reason="hybrid_or_onsite")

    has_remote = bool(REMOTE_RE.search(signal_blob) or REMOTE_RE.search(desc))
    if not has_remote:
        return GateResult(ok=False, reason="not_remote")

    # Prefer place fields for US eligibility; only use JD when place is vague
    place_has_us = _has_us_signal(place_blob) or _has_us_signal(wa)
    if place_has_us:
        return GateResult(ok=True)

    place_vague = (not place_blob) or bool(
        re.fullmatch(
            r"(?i)\s*(remote|worldwide|global|anywhere|distributed|n/?a|-)?\s*",
            loc,
        )
    )
    if place_vague and _has_us_signal(desc) and not _has_non_us_geo(desc[:500]):
        return GateResult(ok=True)

    return GateResult(ok=False, reason="not_us")
