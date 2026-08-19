"""Keep Phong (and similar strategist) resume headers consistent."""
from __future__ import annotations

import re
from typing import Any

_RESUME_IDENTITY_KEYS = (
    "legal_name",
    "email",
    "phone",
    "location_city",
    "location_state",
    "linkedin_url",
    "github_url",
)

_DROP_HEADER_LINE = re.compile(
    r"(?i)(gallus|work authorization|us citizen|u\.s\. citizen|"
    r"authorized to work|citizenship|78660|address_line|"
    r"^\s*\*?\*?email\s*:|^\s*\*?\*?phone\s*:|"
    r"^\s*\*?\*?linkedin\s*:|^\s*\*?\*?github\s*:)"
)

_SECTION_START = re.compile(
    r"(?im)^(?:#{1,3}\s*)?(?:\*\*)?(SUMMARY|TECHNICAL SKILLS|PROFESSIONAL EXPERIENCE|"
    r"SELECTED PROJECTS|EDUCATION)(?:\*\*)?\s*$"
)

_HEADING_HASH = re.compile(
    r"(?im)^#{1,3}\s*((?:SUMMARY|TECHNICAL SKILLS|PROFESSIONAL EXPERIENCE|"
    r"SELECTED PROJECTS|EDUCATION))\s*$"
)


def _bare_url(url: str) -> str:
    text = (url or "").strip()
    text = re.sub(r"^https?://(www\.)?", "", text, flags=re.I)
    return text.rstrip("/")


def resume_location(identity: dict[str, Any] | None) -> str:
    ident = identity or {}
    city = str(ident.get("location_city") or "Pflugerville").strip()
    state = str(ident.get("location_state") or "TX").strip()
    return f"{city}, {state}"


def resume_contact_line(identity: dict[str, Any] | None) -> str:
    ident = identity or {}
    email = str(ident.get("email") or "").strip()
    phone = str(ident.get("phone") or "").strip()
    linkedin = _bare_url(str(ident.get("linkedin_url") or ""))
    github = _bare_url(str(ident.get("github_url") or ""))
    parts = [p for p in (resume_location(ident), email, phone, linkedin, github) if p]
    return " | ".join(parts)


def resume_identity(identity: dict[str, Any] | None) -> dict[str, Any]:
    ident = identity or {}
    out = {k: ident[k] for k in _RESUME_IDENTITY_KEYS if ident.get(k)}
    out["resume_location"] = resume_location(ident)
    out["resume_contact_line"] = resume_contact_line(ident)
    return out


def _extract_title(header: str, display_name: str, default_title: str) -> str:
    title = (default_title or "").strip()
    for raw in header.splitlines():
        line = raw.strip().strip("*").strip()
        if not line or line in {"---", "***", "___"}:
            continue
        if line.casefold() == display_name.casefold():
            continue
        if _DROP_HEADER_LINE.search(line):
            continue
        if "@" in line or "linkedin.com" in line.lower() or "github.com" in line.lower():
            continue
        if re.search(r"\+?\d[\d\s().-]{7,}\d", line):
            continue
        if "Pflugerville" in line and len(line) < 40:
            continue
        if len(line) < 80 and not line.isupper():
            return line
    return title


def enforce_resume_header(md: str, persona: Any) -> str:
    """Replace the generated header with a locked name / title / contact block."""
    ident = getattr(persona, "identity", None) or {}
    display = str(getattr(persona, "display_name", None) or ident.get("legal_name") or "").strip()
    default_title = str(getattr(persona, "default_professional_title", "") or "").strip()
    text = (md or "").strip()
    if not text:
        return text

    match = _SECTION_START.search(text)
    if match:
        header = text[: match.start()]
        body = text[match.start() :]
    else:
        header = text
        body = ""

    title = _extract_title(header, display, default_title)
    contact = resume_contact_line(ident)
    locked = f"{display}\n\n{title}\n\n{contact}\n\n"
    if body:
        body = _HEADING_HASH.sub(r"\1", body)
        return locked + body.lstrip()
    return locked + text
