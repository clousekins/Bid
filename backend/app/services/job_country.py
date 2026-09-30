"""Work out which country a listing is for, from the free text boards give us.

Boards do not report a country. They report a location string, and the strings
are a mess: "Remote", "Remote, US", "United States (Remote)", "LATAM,  Canada,
 USA", "Europe,  UK", "Vancouver, BC", "Blue Bell". There are 283 distinct
spellings across the current 1408 rows.

So the rule is the one the operator asked for: find an explicit country in the
text, and treat everything else as the United States. That is deliberately
biased — a listing that says only "Warsaw" becomes US, because nothing in it
names a country. Getting that wrong costs a row in the wrong filter bucket;
guessing a country from a bare city name would cost more, since city names
repeat across countries (London is in Ontario and in Kentucky as well as
England).

The bias has a useful property: a false match toward US is free, because US is
already the fallback. Only a false match toward some *other* country actually
moves a row. So US spellings are matched liberally and everything else
conservatively.
"""
from __future__ import annotations

import re

DEFAULT = "US"

# code -> display name. Only what the filter needs to show.
NAMES: dict[str, str] = {
    "US": "United States",
    "CA": "Canada",
    "GB": "United Kingdom",
    "IE": "Ireland",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "PT": "Portugal",
    "IT": "Italy",
    "NL": "Netherlands",
    "BE": "Belgium",
    "CH": "Switzerland",
    "AT": "Austria",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "PL": "Poland",
    "CZ": "Czechia",
    "RO": "Romania",
    "UA": "Ukraine",
    "GR": "Greece",
    "TR": "Turkey",
    "IL": "Israel",
    "AE": "United Arab Emirates",
    "IN": "India",
    "PK": "Pakistan",
    "SG": "Singapore",
    "JP": "Japan",
    "KR": "South Korea",
    "CN": "China",
    "HK": "Hong Kong",
    "TW": "Taiwan",
    "TH": "Thailand",
    "VN": "Vietnam",
    "PH": "Philippines",
    "ID": "Indonesia",
    "MY": "Malaysia",
    "AU": "Australia",
    "NZ": "New Zealand",
    "ZA": "South Africa",
    "NG": "Nigeria",
    "KE": "Kenya",
    "EG": "Egypt",
    "MX": "Mexico",
    "BR": "Brazil",
    "AR": "Argentina",
    "CL": "Chile",
    "CO": "Colombia",
    "PE": "Peru",
    "UY": "Uruguay",
    "CR": "Costa Rica",
    "RS": "Serbia",
    "BG": "Bulgaria",
    "HU": "Hungary",
    "HR": "Croatia",
    "LT": "Lithuania",
    "LV": "Latvia",
    "EE": "Estonia",
}

# Spellings that mean each country. Matched as whole words, case-insensitively.
#
# Deliberately absent: "Georgia" (the US state is far likelier here than the
# country) and bare province or state codes like "BC" or "ON" ("on" is an
# English word, and two letters carry too little signal to move a row).
ALIASES: dict[str, tuple[str, ...]] = {
    "US": (
        "united states", "usa", "u.s.a.", "u.s.", "us", "america",
        "united states of america",
    ),
    "CA": ("canada", "canadian"),
    "GB": (
        "united kingdom", "uk", "u.k.", "great britain", "britain", "england",
        "scotland", "wales", "northern ireland", "london, uk",
    ),
    "IE": ("ireland", "republic of ireland", "eire"),
    "DE": ("germany", "deutschland"),
    "FR": ("france",),
    "ES": ("spain", "españa", "espana"),
    "PT": ("portugal",),
    "IT": ("italy", "italia"),
    "NL": ("netherlands", "holland", "the netherlands"),
    "BE": ("belgium",),
    "CH": ("switzerland",),
    "AT": ("austria",),
    "SE": ("sweden",),
    "NO": ("norway",),
    "DK": ("denmark",),
    "FI": ("finland",),
    "PL": ("poland", "polska"),
    "CZ": ("czechia", "czech republic"),
    "RO": ("romania",),
    "UA": ("ukraine",),
    "GR": ("greece",),
    "TR": ("turkey", "türkiye", "turkiye"),
    "IL": ("israel",),
    "AE": ("united arab emirates", "uae", "dubai", "abu dhabi"),
    "IN": ("india",),
    "PK": ("pakistan",),
    "SG": ("singapore",),
    "JP": ("japan",),
    "KR": ("south korea", "korea", "republic of korea"),
    "CN": ("china",),
    "HK": ("hong kong",),
    "TW": ("taiwan",),
    "TH": ("thailand",),
    "VN": ("vietnam", "viet nam"),
    "PH": ("philippines",),
    "ID": ("indonesia",),
    "MY": ("malaysia",),
    "AU": ("australia",),
    "NZ": ("new zealand",),
    "ZA": ("south africa",),
    "NG": ("nigeria",),
    "KE": ("kenya",),
    "EG": ("egypt",),
    "MX": ("mexico", "méxico"),
    "BR": ("brazil", "brasil"),
    "AR": ("argentina",),
    "CL": ("chile",),
    "CO": ("colombia",),
    "PE": ("peru",),
    "UY": ("uruguay",),
    "CR": ("costa rica",),
    "RS": ("serbia",),
    "BG": ("bulgaria",),
    "HU": ("hungary",),
    "HR": ("croatia",),
    "LT": ("lithuania",),
    "LV": ("latvia",),
    "EE": ("estonia",),
}

# "New Mexico" is a US state and must never read as Mexico. Same shape of trap
# for a couple of others, so they are stripped before anything is matched.
_MASK = re.compile(r"\bnew mexico\b|\bmexico,\s*(mo|missouri|ny)\b", re.I)


def _pattern(alias: str) -> re.Pattern[str]:
    # Aliases carry dots ("u.s."), so escape them, and allow the surrounding
    # characters to be anything that is not a letter or digit.
    return re.compile(rf"(?<![0-9A-Za-z]){re.escape(alias)}(?![0-9A-Za-z])", re.I)


_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    code: tuple(_pattern(a) for a in aliases) for code, aliases in ALIASES.items()
}


def country_code(location: str | None) -> str:
    """The country a location string names, or US when it names none.

    Where several countries are named — "LATAM,  Canada,  USA" — the United
    States wins if it is among them, because a posting open to the US is one
    this operator can apply to. Otherwise the earliest named country wins.
    """
    text = _MASK.sub(" ", str(location or ""))
    if not text.strip():
        return DEFAULT

    for pattern in _COMPILED["US"]:
        if pattern.search(text):
            return "US"

    best: tuple[int, str] | None = None
    for code, patterns in _COMPILED.items():
        if code == "US":
            continue
        for pattern in patterns:
            found = pattern.search(text)
            if found and (best is None or found.start() < best[0]):
                best = (found.start(), code)
    return best[1] if best else DEFAULT


def country_name(code: str | None) -> str:
    """A display name for a stored code, falling back to the code itself."""
    key = str(code or DEFAULT).strip().upper()
    return NAMES.get(key, key)


def known_codes() -> list[str]:
    """Every code this module can produce, US first then alphabetical by name."""
    rest = sorted((c for c in NAMES if c != DEFAULT), key=lambda c: NAMES[c])
    return [DEFAULT, *rest]
