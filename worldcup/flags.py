"""Country flags for the 48 teams: small images for HTML, emoji for <option> text."""
from __future__ import annotations

import unicodedata

# normalized team name -> flagcdn / ISO-3166 code (gb-sct, gb-eng for home nations)
ISO = {
    "czechia": "cz", "mexico": "mx", "south africa": "za", "south korea": "kr",
    "bosnia-herzegovina": "ba", "canada": "ca", "qatar": "qa", "switzerland": "ch",
    "brazil": "br", "haiti": "ht", "morocco": "ma", "scotland": "gb-sct",
    "australia": "au", "paraguay": "py", "turkey": "tr", "united states": "us",
    "curacao": "cw", "ecuador": "ec", "germany": "de", "ivory coast": "ci",
    "japan": "jp", "netherlands": "nl", "sweden": "se", "tunisia": "tn",
    "belgium": "be", "egypt": "eg", "iran": "ir", "new zealand": "nz",
    "cape verde islands": "cv", "saudi arabia": "sa", "spain": "es", "uruguay": "uy",
    "france": "fr", "iraq": "iq", "norway": "no", "senegal": "sn",
    "algeria": "dz", "argentina": "ar", "austria": "at", "jordan": "jo",
    "colombia": "co", "congo dr": "cd", "portugal": "pt", "uzbekistan": "uz",
    "croatia": "hr", "england": "gb-eng", "ghana": "gh", "panama": "pa",
}

# Home-nation flags need emoji tag sequences (no simple 2-letter form).
_SPECIAL_EMOJI = {
    "gb-eng": "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
    "gb-sct": "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",
}


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def code_of(name: str) -> str | None:
    return ISO.get(_norm(name))


def flag_img(name: str) -> str:
    """Small PNG (2x) from the flagcdn CDN; '' if unknown."""
    code = code_of(name)
    return f"https://flagcdn.com/w40/{code}.png" if code else ""


def flag_emoji(name: str) -> str:
    code = code_of(name)
    if not code:
        return ""
    if code in _SPECIAL_EMOJI:
        return _SPECIAL_EMOJI[code]
    return "".join(chr(0x1F1E6 + ord(c) - 97) for c in code)
