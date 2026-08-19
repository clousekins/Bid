from __future__ import annotations

import re
from html import unescape

_SECTION_NAMES = {
    "SUMMARY",
    "TECHNICAL SKILLS",
    "PROFESSIONAL EXPERIENCE",
    "SELECTED PROJECTS",
    "EDUCATION",
    "SKILLS",
    "EXPERIENCE",
    "PROJECTS",
    "WORK EXPERIENCE",
}

_BODY = re.compile(r"<body[^>]*>(.*)</body>", re.I | re.S)
_STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style>", re.I | re.S)
_STRIP_TAGS = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"(<(?:p|ul)\b[^>]*>.*?</(?:p|ul)>)", re.I | re.S)


def _text(html_frag: str) -> str:
    t = _STRIP_TAGS.sub("", html_frag or "")
    return unescape(t).replace("\xa0", " ").strip()


def _is_rule(text: str) -> bool:
    return bool(re.fullmatch(r"[-—–_*]{2,}", text or ""))


def _section_key(text: str) -> str | None:
    clean = re.sub(r"[:\s]+$", "", (text or "").strip()).upper()
    return clean if clean in _SECTION_NAMES else None


def _looks_like_contact(text: str) -> bool:
    t = text.lower()
    if "@" in t or t.startswith("http") or "linkedin.com" in t or "github.com" in t:
        return True
    if re.search(r"\+?\d[\d\s().-]{7,}\d", text):
        return True
    if re.search(r"\b\d{5}(?:-\d{4})?\b", text):
        return True
    if re.search(r",\s*(TX|CA|NY|WA|IL|MA|FL|CO|GA|NC|OR|AZ)\b", text, re.I) and len(text) < 90:
        return True
    if re.search(r"\b(Pflugerville|Austin|Remote)\b", text, re.I) and len(text) < 90:
        return True
    return False


def _linkify_contact(text: str) -> str:
    esc = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    if "@" in text and " " not in text.strip():
        return f'<a href="mailto:{esc}">{esc}</a>'
    if text.lower().startswith("http"):
        return f'<a href="{esc}">{esc}</a>'
    if "linkedin.com/" in text.lower() or "github.com/" in text.lower():
        href = "https://" + text.lstrip("/")
        href_esc = (
            href.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return f'<a href="{href_esc}">{esc}</a>'
    return esc


def _looks_like_meta(text: str) -> bool:
    return bool("|" in text or re.search(r"\d{4}", text) or "–" in text or "—" in text)


def replace_document_css(html: str, css: str) -> str:
    """Replace existing <style> blocks (or inject before </head>) with persona CSS."""
    style = f"<style>\n{css}\n</style>"
    if _STYLE_BLOCK.search(html):
        # Replace all style blocks with one persona stylesheet
        html = _STYLE_BLOCK.sub("", html)
    if re.search(r"</head>", html, flags=re.I):
        return re.sub(r"</head>", style + "\n</head>", html, count=1, flags=re.I)
    if re.search(r"<body", html, flags=re.I):
        return re.sub(r"<body", style + "\n<body", html, count=1, flags=re.I)
    return style + "\n" + html


def polish_flat_resume_html(html: str, *, display_name: str = "") -> str:
    """
    Presentation-only: reshape strategist markdown HTML (flat <p>/<ul>) into
    classed structure similar to html_resume_bundle personas. Content unchanged.
    """
    m = _BODY.search(html)
    if not m:
        return html
    body = m.group(1)
    blocks: list[tuple[str, str]] = []
    for mm in _BLOCK.finditer(body):
        frag = mm.group(1)
        kind = "ul" if frag.lower().lstrip().startswith("<ul") else "p"
        blocks.append((kind, frag))
    if not blocks:
        return html

    out: list[str] = ['<div class="resume">']
    i = 0
    current_section: str | None = None
    in_experience = False

    def close_section() -> None:
        nonlocal current_section
        if current_section is not None:
            out.append("</div>")
            current_section = None

    def open_section(key: str) -> None:
        nonlocal current_section, in_experience
        close_section()
        current_section = key
        in_experience = key in {
            "PROFESSIONAL EXPERIENCE",
            "EXPERIENCE",
            "WORK EXPERIENCE",
        }
        out.append('<div class="section">')
        out.append(f'<div class="section-title">{key}</div>')

    # --- Header ---
    if blocks[0][0] == "p":
        name = _text(blocks[0][1])
        if not _section_key(name) and not _is_rule(name):
            out.append('<div class="header">')
            out.append(f'<div class="name">{name}</div>')
            i = 1
            if i < len(blocks) and blocks[i][0] == "p" and _is_rule(_text(blocks[i][1])):
                i += 1
            # Optional title line
            if i < len(blocks) and blocks[i][0] == "p":
                t = _text(blocks[i][1])
                if (
                    not _section_key(t)
                    and not _is_rule(t)
                    and not _looks_like_contact(t)
                    and len(t) < 100
                    and not _looks_like_meta(t)
                ):
                    out.append(f'<div class="title">{t}</div>')
                    i += 1
                    if i < len(blocks) and blocks[i][0] == "p" and _is_rule(_text(blocks[i][1])):
                        i += 1
            contacts: list[str] = []
            while i < len(blocks) and blocks[i][0] == "p":
                t = _text(blocks[i][1])
                if _is_rule(t):
                    i += 1
                    continue
                if _section_key(t):
                    break
                if _looks_like_contact(t) or (len(t) < 70 and not t.isupper()):
                    pieces = [p.strip() for p in re.split(r"\s*[|·•]\s*", t) if p.strip()]
                    if len(pieces) > 1:
                        contacts.extend(_linkify_contact(p) for p in pieces)
                    else:
                        contacts.append(_linkify_contact(t))
                    i += 1
                    continue
                break
            if contacts:
                joined = '<span class="sep"> · </span>'.join(contacts)
                out.append(f'<div class="contact">{joined}</div>')
            out.append("</div>")

    # --- Body sections ---
    while i < len(blocks):
        kind, frag = blocks[i]
        if kind == "p":
            t = _text(frag)
            if _is_rule(t):
                i += 1
                continue
            sk = _section_key(t)
            if sk:
                open_section(sk)
                i += 1
                continue

            if in_experience:
                company = t
                role = ""
                meta = ""
                j = i + 1
                if j < len(blocks) and blocks[j][0] == "p":
                    t2 = _text(blocks[j][1])
                    if not _section_key(t2) and not _is_rule(t2):
                        role = t2
                        j += 1
                if j < len(blocks) and blocks[j][0] == "p":
                    t3 = _text(blocks[j][1])
                    if not _section_key(t3) and not _is_rule(t3) and _looks_like_meta(t3):
                        meta = t3
                        j += 1
                out.append('<div class="job">')
                out.append('<div class="job-header">')
                # Phong pattern: company, then role, then location|dates
                out.append(f'<div class="job-company">{company}</div>')
                if meta:
                    out.append(f'<div class="job-meta">{meta}</div>')
                out.append("</div>")
                if role:
                    out.append(f'<div class="job-role">{role}</div>')
                i = j
                if i < len(blocks) and blocks[i][0] == "ul":
                    out.append(blocks[i][1])
                    i += 1
                out.append("</div>")
                continue

            section_u = current_section or ""
            if section_u == "SUMMARY":
                inner = re.sub(r"^<p>|</p>$", "", frag, flags=re.I).strip()
                out.append(f'<p class="summary">{inner}</p>')
            elif "SKILL" in section_u:
                out.append(frag.replace("<p>", '<p class="skills-line">', 1))
            elif section_u == "EDUCATION":
                inner = re.sub(r"^<p>|</p>$", "", frag, flags=re.I).strip()
                out.append(f'<div class="edu"><div class="edu-degree">{inner}</div></div>')
            else:
                out.append(frag)
            i += 1
            continue

        # ul
        section_u = current_section or ""
        if "SKILL" in section_u:
            out.append(frag.replace("<ul>", '<ul class="skills">', 1))
        else:
            out.append(frag)
        i += 1

    close_section()
    out.append("</div>")
    new_body = "\n".join(out)
    return _BODY.sub(lambda _m: f"<body>\n{new_body}\n</body>", html, count=1)
