"""Keyword-based exclusion of land/investor/teardown listings.

This is the non-AI filtering approach discussed with the client: a plain,
case-insensitive substring match against the listing description (and
optionally other fields declared via `match_field` in excluded_keywords.csv).
"""
from typing import List, Optional, Tuple

from .models import Listing


def find_matching_keyword(listing: Listing, keywords: List[dict]) -> Optional[Tuple[str, str]]:
    """Return (keyword, notes) of the first matching excluded keyword, or None."""
    haystacks = {
        "description": (listing.description or "").lower(),
        "property_type": (listing.property_type or "").lower(),
        "building_type": (listing.building_type or "").lower(),
        "full_address": (listing.full_address or "").lower(),
    }
    combined = " ".join(haystacks.values())

    for entry in keywords:
        field = entry["match_field"]
        haystack = haystacks.get(field, combined) if field != "any" else combined
        if entry["keyword"] in haystack:
            return entry["keyword"], entry["notes"]
    return None


def apply_keyword_filter(listing: Listing, keywords: List[dict]) -> Listing:
    match = find_matching_keyword(listing, keywords)
    if match:
        listing.excluded = True
        listing.excluded_keyword = match[0]
    return listing
