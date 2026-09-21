"""Splits realtor.ca's combined address string into Unit / Street / Province / Postal Code.

realtor.ca formats the address differently depending on which MLS board a
listing comes from:

  - CREB (Calgary) style, unit comma-separated:
        "408, 310 12 Avenue SW, Calgary, Alberta T2R1B5"
  - TREB/other-board style, unit hyphen-separated within the street segment:
        "2 - 427 KEATS WAY, Waterloo, Ontario N2L5S7"
  - Compact hyphen style (no spaces), e.g. as quoted by the client:
        "115-1925 18 Ave NE, Calgary, AB T2E 7T8"
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
COMMA_UNIT_RE = re.compile(r"^[A-Za-z0-9]{1,6}$")

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


def parse_address(full_address: str) -> dict:
    """Return {"unit", "street_address", "province", "postal_code"} parsed from a
    realtor.ca combined address string. Missing/unparseable fields are ""."""
    result = {"unit": "", "street_address": "", "province": "", "postal_code": ""}
    if not full_address or not full_address.strip():
        return result

    parts = [p.strip() for p in full_address.split(",") if p.strip()]
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
    elif len(parts) == 2:
        street_parts = parts[:1]
    else:
        street_parts = []

    unit = ""
    if len(street_parts) >= 2 and COMMA_UNIT_RE.match(street_parts[0]):
        # e.g. ["408", "310 12 Avenue SW"] - CREB-style comma-separated unit.
        unit = street_parts[0]
        street_parts = street_parts[1:]

    street = ", ".join(street_parts).strip()

    if not unit:
        m = UNIT_RE.match(street)
        if m and re.search(r"[A-Za-z]", m.group(2)):
            unit, street = m.group(1), m.group(2).strip()

    result["unit"] = unit
    result["street_address"] = street
    return result
