from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI

from app.config import get_settings
from app.services.openai_compat import chat_completion

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
_TITLE_HINTS = re.compile(
    r"(?im)^(?:job\s*title|title|position|role)\s*[:\-]\s*(.+)$"
)
_COMPANY_HINTS = re.compile(
    r"(?im)^(?:company|employer|organization|about\s+.+?)\s*[:\-]\s*(.+)$"
)


def _heuristic_extract(jd: str) -> dict[str, str]:
    text = (jd or "").strip()
    urls = _URL_RE.findall(text)
    url = ""
    for candidate in urls:
        cleaned = candidate.rstrip(").,;]")
        low = cleaned.lower()
        if any(x in low for x in ("linkedin.com/jobs", "greenhouse.io", "lever.co", "ashbyhq.com", "workday", "boards.", "jobs.", "careers", "apply")):
            url = cleaned
            break
    if not url and urls:
        url = urls[0].rstrip(").,;]")

    title = ""
    m = _TITLE_HINTS.search(text)
    if m:
        title = m.group(1).strip()[:180]

    company = ""
    m = _COMPANY_HINTS.search(text)
    if m:
        company = m.group(1).strip()[:120]

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not title and lines:
        # First short-ish line often is the role
        for ln in lines[:8]:
            if 8 <= len(ln) <= 90 and "http" not in ln.lower():
                title = ln
                break
    location = ""
    for ln in lines[:20]:
        low = ln.lower()
        if any(k in low for k in ("remote", "hybrid", "on-site", "onsite", "united states", ", ca", ", ny", ", tx")):
            if len(ln) <= 80:
                location = ln
                break
    return {
        "company": company,
        "title": title,
        "url": url,
        "location": location,
    }


async def extract_job_fields_from_jd(jd: str) -> dict[str, Any]:
    """Extract company, title, url, location from a pasted JD."""
    base = _heuristic_extract(jd)
    settings = get_settings()
    if not settings.openai_api_key:
        return {
            **base,
            "company": base["company"] or "Unknown Company",
            "title": base["title"] or "Unknown Role",
            "url": base["url"] or "",
            "location": base["location"] or "Remote, US",
            "source": "heuristic",
        }

    system = (
        "Extract structured job metadata from a pasted job description. "
        "Return STRICT JSON only with keys: company, title, url, location. "
        "Use only information present in the text. "
        "url must be an http(s) apply/job link found in the text, else empty string. "
        "location should be city/state/remote if present, else empty string. "
        "Do not invent employers or roles that are not supported by the JD."
    )
    user = {
        "job_description": (jd or "")[:16000],
        "heuristic_guess": base,
    }
    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0, max_retries=1)
        resp = await chat_completion(
            client,
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.M)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not_object")
        company = str(data.get("company") or base["company"] or "").strip()
        title = str(data.get("title") or base["title"] or "").strip()
        url = str(data.get("url") or base["url"] or "").strip()
        location = str(data.get("location") or base["location"] or "").strip()
        if url and not url.lower().startswith(("http://", "https://")):
            url = base["url"]
        return {
            "company": company or "Unknown Company",
            "title": title or "Unknown Role",
            "url": url,
            "location": location or "Remote, US",
            "source": "llm",
        }
    except Exception:  # noqa: BLE001
        return {
            "company": base["company"] or "Unknown Company",
            "title": base["title"] or "Unknown Role",
            "url": base["url"] or "",
            "location": base["location"] or "Remote, US",
            "source": "heuristic",
        }
