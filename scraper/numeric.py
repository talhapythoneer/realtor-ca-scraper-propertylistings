"""Converts realtor.ca's display-formatted numbers into clean, sortable int/float values.

realtor.ca shows these as formatted text, not plain numbers:
  - price:           "$698,000"
  - bedrooms:         "3 + 2"     (above-grade + below-grade/basement, per MLS convention)
  - bathrooms:        "3"
  - square_footage:   "1211" or "1200+" or "1000-1199" (range bucket when no exact figure is on file)
  - storeys:          "1"

These helpers strip the formatting so the value can be written as a real
number (Excel numeric cell / plain CSV number) instead of text, so the
columns sort and filter correctly.
"""
import re
from typing import Optional, Union

Number = Union[int, float]

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _int_if_whole(value: float) -> Number:
    return int(value) if value.is_integer() else value


def clean_price(value) -> Optional[Number]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    digits = re.sub(r"[^\d.]", "", str(value))
    if not digits:
        return None
    return _int_if_whole(float(digits))


def clean_bedrooms(value) -> Optional[Number]:
    """'3 + 2' -> 5 (above-grade + below-grade total, for sorting/filtering by total bedroom count)."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    nums = [float(n) for n in _NUM_RE.findall(str(value))]
    if not nums:
        return None
    return _int_if_whole(sum(nums))


def clean_leading_number(value) -> Optional[Number]:
    """First number in a value like '1200+' or '1000-1199' -> 1200 / 1000."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    match = _NUM_RE.search(str(value).replace(",", ""))
    if not match:
        return None
    return _int_if_whole(float(match.group()))
