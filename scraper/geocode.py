"""Resolves a city to the search area realtor.ca's listing search needs.

realtor.ca's search takes either:
  - `GeoIds` - a stable id for a city's administrative boundary, or
  - a map rectangle (`LatitudeMin/Max`, `LongitudeMin/Max`) - what realtor.ca's
    own search box falls back to for places that have no boundary of their own.

GeoIds come from realtor.ca's public SEO city landing pages (e.g.
https://www.realtor.ca/ab/calgary/real-estate), which embed a
`model.SEOLandingPageCriteria` string with the city's GeoIds. (The site's
search-box autocomplete endpoint would also give them, but it's heavily
bot-guarded - it returned a hard 403 "blocked" in testing.)

Small/unincorporated places (Bragg Creek, Langdon, Bowser, Chemainus, ...) have
a landing page but no GeoIds, and searching by URL path alone returns nothing.
For those, the search area is a map rectangle instead, taken from (in order):
  1. a `map_url` in that row of input.csv - a realtor.ca map URL copied from the
     browser's address bar after searching for the town on realtor.ca itself, or
  2. OpenStreetMap's geocoder (Nominatim), looked up once and cached.

Results are cached in data/geo_cache.json so each city is only resolved once.
"""
import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlparse

from curl_cffi import requests as curl_requests

from .address import expand_province
from .browser import is_blocked_page, is_dead_browser_error

logger = logging.getLogger("realtor_scraper")

BASE_URL = "https://www.realtor.ca"
CRITERIA_RE = re.compile(r"SEOLandingPageCriteria\s*=\s*'([^']+)'")
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "realtor-ca-new-listings-scraper/2.0"
BBOX_KEYS = ("LatitudeMin", "LatitudeMax", "LongitudeMin", "LongitudeMax")


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


def has_search_area(params: Optional[dict]) -> bool:
    return bool(params) and (bool(params.get("GeoIds")) or all(params.get(k) for k in BBOX_KEYS))


def describe_area(params: dict) -> str:
    if params.get("GeoIds"):
        return f"GeoIds={params['GeoIds']}"
    return "map area lat {LatitudeMin}..{LatitudeMax}, lng {LongitudeMin}..{LongitudeMax}".format(**params)


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


def params_from_map_url(map_url: str) -> Optional[dict]:
    """Search area from a realtor.ca map URL, e.g.
    https://www.realtor.ca/map#ZoomLevel=14&...&LatitudeMax=50.98442&LongitudeMax=-113.62459&..."""
    fragment = urlparse(map_url.strip()).fragment or map_url
    query = dict(parse_qsl(fragment, keep_blank_values=True))
    params = {}
    if query.get("GeoIds"):
        params["GeoIds"] = query["GeoIds"]
    elif all(query.get(k) for k in BBOX_KEYS):
        params.update({k: query[k] for k in BBOX_KEYS})
    else:
        return None
    params["_source"] = "map_url"
    return params


def geocode_bbox(city: str, province: str, padding_km: float = 0.0) -> Optional[dict]:
    """Map rectangle for a place from OpenStreetMap's geocoder, padded by `padding_km` on each side."""
    query = f"{city}, {expand_province(province)}, Canada"
    try:
        response = curl_requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "ca"},
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=30,
        )
        time.sleep(1.1)  # Nominatim's usage policy: at most one request per second
        results = response.json() if response.status_code == 200 else []
    except Exception as exc:
        logger.error("Could not look up '%s' on OpenStreetMap: %s", query, exc)
        return None
    if not results:
        logger.error("OpenStreetMap has no match for '%s'.", query)
        return None

    lat_min, lat_max, lng_min, lng_max = (float(v) for v in results[0]["boundingbox"])
    if padding_km:
        lat_pad = padding_km / 111.0
        lng_pad = padding_km / (111.0 * math.cos(math.radians((lat_min + lat_max) / 2)))
        lat_min, lat_max, lng_min, lng_max = lat_min - lat_pad, lat_max + lat_pad, lng_min - lng_pad, lng_max + lng_pad
    logger.info("Located '%s' via OpenStreetMap: %s", query, results[0].get("display_name", ""))
    return {
        "LatitudeMin": f"{lat_min:.5f}",
        "LatitudeMax": f"{lat_max:.5f}",
        "LongitudeMin": f"{lng_min:.5f}",
        "LongitudeMax": f"{lng_max:.5f}",
    }


def resolve_geo(driver, province: str, slug: str, city_label: str) -> Optional[dict]:
    """Fetch a city's SEO landing page and pull its GeoIds + default search criteria out of it.

    Returns {} (not None) when the page loaded but has no criteria at all, so the
    caller can still fall back to a map rectangle."""
    url = seo_landing_url(province, slug)
    logger.info("Resolving map location for '%s' via %s", city_label, url)

    try:
        driver.get(url)
    except Exception as exc:
        if is_dead_browser_error(exc):
            raise
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

    match = CRITERIA_RE.search(driver.page_source)
    if not match:
        logger.warning("No realtor.ca city page found at %s for '%s'.", url, city_label)
        return {}

    params = _parse_criteria(match.group(1))
    params["_seo_url"] = url
    return params


def get_geo_params(driver, cache: dict, row, geo_cfg: dict, force_refresh: bool = False) -> Optional[dict]:
    """Return the search area for a SearchRow (GeoIds or a map rectangle), resolving and caching it if needed."""
    if row.map_url:
        params = params_from_map_url(row.map_url)
        if not params:
            logger.error(
                "The map_url for '%s' has no GeoIds or LatitudeMin/Max + LongitudeMin/Max in it - "
                "copy the whole address bar from a realtor.ca map search.", row.city,
            )
        return params

    slug = row.seo_slug.strip() if row.seo_slug else slugify_city(row.city)
    cache_key = f"{row.province.strip().lower()}/{slug}"
    params = None if force_refresh else cache.get(cache_key)

    if params is None:
        params = resolve_geo(driver, row.province, slug, row.city)
        if params is None:
            return None  # page failed to load/blocked - don't cache, try again next run

    if not has_search_area(params):
        # Small/unincorporated places: realtor.ca has a page but no boundary for them,
        # so search the map area around the town instead, as realtor.ca's own search box does.
        bbox = geocode_bbox(row.city, row.province, float(geo_cfg.get("bbox_padding_km", 0) or 0))
        if not bbox:
            logger.error(
                "'%s' has no realtor.ca boundary and couldn't be located on OpenStreetMap either. "
                "Search for it on realtor.ca, then paste the map URL into this row's map_url column in input.csv.",
                row.city,
            )
            return None
        params = {**params, **bbox, "_source": "openstreetmap"}

    if params.get("GeoIds"):
        logger.debug("'%s' -> GeoIds=%s", row.city, params["GeoIds"])
    cache[cache_key] = params
    return params
