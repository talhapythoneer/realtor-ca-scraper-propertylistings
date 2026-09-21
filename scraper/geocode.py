"""Resolves a city to the realtor.ca map GeoIds it uses internally for search.

realtor.ca's map search will not accept a plain city name - it needs a
resolved `GeoIds` value (a stable id for that city's administrative boundary).
The site's own homepage search box can resolve this, but that flow goes
through a heavily bot-guarded AJAX endpoint
(`/Services/Actions.asmx/GetAutocompleteResults`) that returned a hard 403
"blocked" response in testing, even on a fresh browser session.

Instead, we get the same GeoIds from realtor.ca's own SEO-friendly city
landing pages (e.g. https://www.realtor.ca/ab/calgary/real-estate) - plain,
server-rendered GET requests that embed a `model.SEOLandingPageCriteria`
string containing the GeoIds plus the site's own default search criteria for
that city. These pages proved reliable in testing and, as a bonus, already
contain a page of real listing cards, using the exact same markup as a normal
map search result.

Results are cached in data/geo_cache.json so this lookup only ever needs to
happen once per city.
"""
import json
import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl

from .browser import is_blocked_page

logger = logging.getLogger("realtor_scraper")

BASE_URL = "https://www.realtor.ca"
CRITERIA_RE = re.compile(r"SEOLandingPageCriteria\s*=\s*'([^']+)'")


def slugify_city(city: str) -> str:
    """Best-effort conversion of a city name to realtor.ca's URL slug format.

    Works for the simple/common cases (e.g. "High River" -> "high-river").
    Multi-municipality names (the two North Vancouvers, the two Langleys,
    recently-amalgamated towns, etc.) may need a manual `seo_slug` override in
    input.csv - see the notes column and the README troubleshooting section.
    """
    slug = city.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)  # drop punctuation e.g. periods, apostrophes, parentheses
    slug = re.sub(r"\s+", "-", slug)
    return slug


def seo_landing_url(province: str, slug: str) -> str:
    return f"{BASE_URL}/{province.strip().lower()}/{slug}/real-estate"


def load_geo_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read geo cache %s (%s); starting fresh.", cache_path, exc)
        return {}


def save_geo_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)


def _parse_criteria(criteria: str) -> dict:
    return dict(parse_qsl(criteria, keep_blank_values=True))


def resolve_geo(driver, province: str, slug: str, city_label: str) -> Optional[dict]:
    """Fetch a city's SEO landing page and pull the GeoIds + default search criteria out of it."""
    url = seo_landing_url(province, slug)
    logger.info("Resolving map location for '%s' via %s", city_label, url)

    try:
        driver.get(url)
    except Exception as exc:
        logger.error("Could not load %s for '%s': %s", url, city_label, exc)
        return None

    if is_blocked_page(driver):
        logger.error(
            "%s was blocked by realtor.ca's bot protection while resolving '%s'. "
            "Try again later, or with headless: false / a proxy - see README.",
            url,
            city_label,
        )
        return None

    html = driver.page_source
    match = CRITERIA_RE.search(html)
    if not match:
        logger.error(
            "No SEOLandingPageCriteria found on %s for '%s'. The page may not exist for this city - "
            "set an explicit `seo_slug` for this row in input.csv (see README).",
            url,
            city_label,
        )
        return None

    params = _parse_criteria(match.group(1))
    params["_seo_url"] = url

    if "GeoIds" in params:
        logger.info("Resolved '%s' -> GeoIds=%s", city_label, params["GeoIds"])
    else:
        # Small/unincorporated places (hamlets, etc.) don't always have their own
        # boundary polygon on realtor.ca. The SEO page's URL path still scopes
        # search results to the right place without GeoIds - confirmed against
        # a real working example URL for Bragg Creek, AB.
        logger.info("Resolved '%s' with no GeoIds (small/unincorporated place) - scoping by URL path instead.", city_label)

    return params


def get_geo_params(driver, cache: dict, row, force_refresh: bool = False) -> Optional[dict]:
    """Return cached geo/criteria params for a SearchRow, resolving (and caching) if needed."""
    slug = row.seo_slug.strip() if row.seo_slug else slugify_city(row.city)
    cache_key = f"{row.province.strip().lower()}/{slug}"

    if not force_refresh and cache_key in cache:
        return cache[cache_key]

    params = resolve_geo(driver, row.province, slug, row.city)
    if params:
        cache[cache_key] = params
    return params
