"""Helpers for turning realtor.ca's relative 'time on realtor.ca' text into a date."""
import re
from datetime import datetime, timedelta
from typing import Optional

_UNIT_TO_KWARG = {
    "minute": "minutes",
    "minutes": "minutes",
    "hour": "hours",
    "hours": "hours",
    "day": "days",
    "days": "days",
    "week": "weeks",
    "weeks": "weeks",
    "month": "days",  # approximate a month as 30 days
    "months": "days",
    "year": "days",  # approximate a year as 365 days
    "years": "days",
}
_MONTH_UNITS = {"month", "months"}
_YEAR_UNITS = {"year", "years"}

_RELATIVE_RE = re.compile(
    r"(\d+)\s*\+?\s*(minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)", re.I
)


def estimate_listed_date(relative_text: str, scraped_at: Optional[datetime] = None) -> str:
    """Convert a string like '3 hours ago' / '2 days ago' / 'Yesterday' / 'New' into a YYYY-MM-DD date.

    Falls back to the scrape date when the text can't be parsed (safer than guessing wrong).
    """
    scraped_at = scraped_at or datetime.now()
    if not relative_text:
        return scraped_at.date().isoformat()

    text = relative_text.strip().lower()

    if "yesterday" in text:
        return (scraped_at - timedelta(days=1)).date().isoformat()

    if any(token in text for token in ("just now", "new", "moments ago", "today")):
        return scraped_at.date().isoformat()

    match = _RELATIVE_RE.search(text)
    if not match:
        return scraped_at.date().isoformat()

    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit in _MONTH_UNITS:
        amount *= 30
    elif unit in _YEAR_UNITS:
        amount *= 365
    kwarg = _UNIT_TO_KWARG[unit]
    delta = timedelta(**{kwarg: amount})
    return (scraped_at - delta).date().isoformat()
