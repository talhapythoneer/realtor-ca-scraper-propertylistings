"""Splits realtor.ca's combined address string into Unit / Street / Province / Postal Code.

realtor.ca formats the address differently depending on which MLS board a
listing comes from:

  - CREB (Calgary) style, unit comma-separated:
        "408, 310 12 Avenue SW, Calgary, Alberta T2R1B5"
  - TREB/other-board style, unit hyphen-separated within the street segment:
        "2 - 427 KEATS WAY, Waterloo, Ontario N2L5S7"
  - Compact hyphen style (no spaces), e.g. as quoted by the client:
        "115-1925 18 Ave NE, Calgary, AB T2E 7T8"
  - BC boards (Metro Vancouver, Vancouver Island), unit space-separated -
    no comma or hyphen at all, so it's only recognisable as "two numbers
    in a row, then a street name":
        "2203 305 MORRISSEY ROAD, Port Moody, British Columbia V3H0M3"
  - No unit at all:
        "1312 48 Avenue NW, Calgary, Alberta T2K5Y3"
  - Card-only (no detail page fetched): no postal code, trailing space:
        "3803 12 AV NW, Edmonton, Alberta "

All of these are handled here. Any field that can't be confidently parsed is
left as "" rather than guessed.
"""
import re
from typing import Optional

POSTAL_RE = re.compile(r"([A-Za-z]\d[A-Za-z])\s?(\d[A-Za-z]\d)\s*$")
UNIT_RE = re.compile(r"^\s*([A-Za-z0-9]{1,6})\s*-\s*(.+)$")
# A single unit ("408", "#B2"), or two combined units sold together ("506 507", "12/14").
COMMA_UNIT_RE = re.compile(r"^#?(?:[A-Za-z0-9]{1,6}|\d{1,5}(?:\s*[/&]\s*|\s+)\d{1,5})$")
# "<unit> <civic number> <street>" - BC style. The unit is a number, optionally
# with a short letter prefix/suffix ("PH2", "TH5", "12B") or a lone letter ("A").
SPACE_UNIT_RE = re.compile(r"^#?([A-Za-z]{0,3}\d{1,5}[A-Za-z]?|[A-Za-z])\s+(\d{1,6}[A-Za-z]?)\s+(.+)$")

# Words that can make up the *rest* of a numbered street's name. If everything
# after "<number> <number>" is only these, the second number is the street
# itself ("5993 143 STREET", "1234 56A Avenue NW", "14601 55 A AVENUE") and there's no unit.
_STREET_TYPE_WORDS = {
    "street", "st", "avenue", "ave", "av", "road", "rd", "drive", "dr", "boulevard", "blvd",
    "way", "crescent", "cres", "cr", "place", "pl", "lane", "ln", "court", "crt", "ct",
    "trail", "tr", "trl", "highway", "hwy", "terrace", "terr", "close", "gate", "parkway", "pkwy",
    "square", "sq", "circle", "cir", "common", "link", "line", "range", "rr", "township", "twp",
    "loop", "bay", "point", "row", "walk", "mews", "green", "heights", "hill", "view", "landing",
    "grove", "cove", "path", "rise", "ridge", "park", "crossing", "wynd", "run", "glen", "heath",
    "vista", "estates", "villas", "chase",
    "a", "b", "c", "d",  # "14601 55 A AVENUE" = 55A Avenue
    "n", "s", "e", "w", "ne", "nw", "se", "sw", "north", "south", "east", "west",
}

PROVINCE_ABBR_TO_NAME = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}


def expand_province(text: str) -> str:
    """'AB' -> 'Alberta'; anything already spelled out (or unrecognized) passes through as-is."""
    text = (text or "").strip()
    if len(text) == 2 and text.upper() in PROVINCE_ABBR_TO_NAME:
        return PROVINCE_ABBR_TO_NAME[text.upper()]
    return text


def _split_space_unit(street: str):
    """("2203", "305 MORRISSEY ROAD") for a BC-style "2203 305 MORRISSEY ROAD", else None."""
    m = SPACE_UNIT_RE.match(street)
    if not m:
        return None
    rest = m.group(3)
    # Three numbers in a row ("1 5993 143 STREET", "202 199 31st St") is always
    # unit + civic number + numbered street.
    if not re.match(r"\d", rest):
        rest_words = re.findall(r"[A-Za-z]+", rest)
        if not rest_words or all(word.lower() in _STREET_TYPE_WORDS for word in rest_words):
            return None  # "5993 143 STREET" - a numbered street, not a unit
    return m.group(1), f"{m.group(2)} {m.group(3)}".strip()


def parse_address(full_address: str) -> dict:
    """Return {"unit", "street_address", "address_city", "province", "postal_code"}
    parsed from a realtor.ca combined address string. Missing/unparseable fields are ""."""
    result = {"unit": "", "street_address": "", "address_city": "", "province": "", "postal_code": ""}
    if not full_address or not full_address.strip():
        return result

    # The search API separates street and city with "|" instead of ", ".
    parts = [p.strip() for p in re.split(r"[,|]", full_address) if p.strip()]
    if not parts:
        return result

    tail = parts[-1]
    postal_match = POSTAL_RE.search(tail)
    if postal_match:
        result["postal_code"] = f"{postal_match.group(1)}{postal_match.group(2)}".upper()
        province_text = tail[: postal_match.start()].strip()
    else:
        province_text = tail
    result["province"] = expand_province(province_text)

    # parts[-2] is the city (when present) - everything before that is the street segment.
    if len(parts) >= 3:
        street_parts = parts[:-2]
        result["address_city"] = parts[-2]
    elif len(parts) == 2:
        street_parts = parts[:1]
    else:
        street_parts = []

    unit = ""
    if len(street_parts) >= 2 and COMMA_UNIT_RE.match(street_parts[0]):
        # e.g. ["408", "310 12 Avenue SW"] - CREB-style comma-separated unit.
        unit = street_parts[0].lstrip("#")
        street_parts = street_parts[1:]

    street = ", ".join(street_parts).strip()

    if not unit:
        m = UNIT_RE.match(street)
        if m and re.search(r"[A-Za-z]", m.group(2)):
            unit, street = m.group(1), m.group(2).strip()

    if not unit:
        split = _split_space_unit(street)
        if split:
            unit, street = split

    result["unit"] = unit
    result["street_address"] = street
    return result
