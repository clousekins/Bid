"""Pull a pay range out of a job description, with no LLM and no network.

Boards rarely give salary as a field, so it is read from the JD text. A range
wins over a single figure. Away from any salary wording only a salary-sized
range counts, so a "$10 - $20 copay" or a "$5 million raised" line cannot pose
as pay; next to a salary cue the rule relaxes to also catch hourly figures and a
lone number. Whatever this returns is a first guess: the field is editable, so a
wrong read is one keystroke to fix.
"""
from __future__ import annotations

import re

# A salary-sized amount: $120,000 / $120,000.50 / $120K / $120000 (4+ digits).
_BIG = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{2,3}(?:\.\d{1,2})?\s*[kK]|\d{4,}"
# An hourly-sized amount: $25 / $75.50. Only trusted next to a salary cue.
_SMALL = r"\d{1,3}(?:\.\d{1,2})?"
_BIG_AMT = rf"\$\s*(?:{_BIG})"
_ANY_AMT = rf"\$\s*(?:{_BIG}|{_SMALL})"
# A per-unit suffix: /yr, per year, annually, /hour, an hour …
_UNIT = (
    r"(?:\s*(?:/|per\s+)\s*(?:yr|year|hour|hr|hrs|mo|month|annum|wk|week)"
    r"|\s*(?:annually|yearly|hourly|a\s+year|an\s+hour|per\s+annum))"
)
# Two amounts joined by a dash or the word "to".
_SEP = r"(?:\s*[-–—]\s*|\s+to\s+)"

_RANGE_BIG = re.compile(rf"(?:{_BIG_AMT}){_SEP}(?:{_BIG_AMT})(?:{_UNIT})?", re.I)
_RANGE_ANY = re.compile(rf"(?:{_ANY_AMT}){_SEP}(?:{_ANY_AMT})(?:{_UNIT})?", re.I)
# A lone figure counts only with a per-unit suffix pinning it as pay.
_SINGLE_ANY = re.compile(rf"(?:{_ANY_AMT})(?:{_UNIT})", re.I)
_BARE_BIG = re.compile(_BIG_AMT, re.I)

# Where pay tends to be announced, so a nearby figure is almost certainly it.
_CUE_RE = re.compile(
    r"(?i)(?:salary|compensation|total\s+comp|base\s+pay|base\s+salary|"
    r"pay\s+range|pay\s+rate|hourly\s+rate|wage|annual\s+(?:salary|pay))"
)

_WINDOW = 240


def _clean(match: str) -> str:
    return re.sub(r"\s+", " ", match).strip(" .,:;-–—")


def _ok(found: str | None) -> str | None:
    return found if found and 3 <= len(found) <= 60 else None


def _find_near_cue(segment: str) -> str | None:
    """Permissive: a cue vouches for hourly figures and a lone number."""
    for rx in (_RANGE_ANY, _SINGLE_ANY, _BARE_BIG):
        m = rx.search(segment)
        if m:
            return _clean(m.group(0))
    return None


def extract_salary(jd: str | None) -> str | None:
    """Best-guess pay range from a JD, or None when nothing credible is found."""
    text = jd or ""
    if len(text.strip()) < 10:
        return None

    # 1. A figure right after a salary cue is the most trustworthy signal.
    for cue in _CUE_RE.finditer(text):
        found = _ok(_find_near_cue(text[cue.start() : cue.start() + _WINDOW]))
        if found:
            return found

    # 2. Away from any cue, only a salary-sized range or a unit-bearing figure
    #    (hourly rates included) is safe enough to take on its own.
    for rx in (_RANGE_BIG, _SINGLE_ANY):
        m = rx.search(text)
        if m:
            found = _ok(_clean(m.group(0)))
            if found:
                return found
    return None
