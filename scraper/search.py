"""Builds realtor.ca search filters, pages through results, and parses listing cards.

Filtered/paginated search runs on the city's own SEO landing page (e.g.
/ab/bragg-creek/real-estate) rather than the generic /map page - the /map page
needs heavier map-specific JS bundles that proved unreliable in testing, and a
real working example URL confirmed the SEO page path + a #view=list hash is
what the site itself uses for this.

Navigation mirrors how a real user's browser actually applies a filter change
on this page: land on it once, then update `location.hash` and dispatch a
`hashchange` event in-page to apply the initial filters (price/date/sort),
rather than doing a fresh full page load. Combined with driving the browser
via undetected_chromedriver (non-headless - see browser.py), this is what got
realtor.ca's live search actually returning results in testing, after
Playwright (even via the real installed Chrome) consistently could not get
past the site's protection at the point a search executes.

Pagination beyond page 1, however, does NOT work by rewriting `CurrentPage`
in the hash - confirmed directly in testing: every "page" requested that way
returned identical results, silently. Clicking the site's own visible "next
page" control does work (its hidden state carries forward GeoIds/GeoName
context that our hash rewrite didn't reconstruct), so subsequent pages are
advanced via a real click on that control instead.
"""
import logging
import random
import re
import time
from typing import List, Optional, Tuple
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By

from .browser import get_network_failures, is_blocked_page
from .geocode import seo_landing_url, slugify_city
from .models import Listing, SearchRow

logger = logging.getLogger("realtor_scraper")

BASE_URL = "https://www.realtor.ca"
CARD_SELECTOR = "div.listingCard"
RELATIVE_TIME_RE = re.compile(r"\b\d+\s*\+?\s*(minute|hour|day|week|month)s?\b", re.I)

# Sub-resources observed in testing to be blocked by realtor.ca's bot protection
# specifically at search-execution time, distinct from the main page (which
# loads fine). If either of these errors out, no amount of waiting/retrying
# the same request will help.
GUARDED_RESOURCE_PATTERNS = ("/bundles/js/desktop/", "/ping.html")

RESULTS_READY_JS = (
    "return document.querySelector("
    "'div.listingCard, #mapNoSidebarResultsCon, #mapSideBarNoResults') !== null;"
)

SET_HASH_JS = """
window.location.hash = arguments[0];
window.dispatchEvent(new HashChangeEvent('hashchange'));
"""


def resolve_base_url(geo_params: dict, row: SearchRow) -> str:
    return geo_params.get("_seo_url") or seo_landing_url(
        row.province, row.seo_slug.strip() if row.seo_slug else slugify_city(row.city)
    )


def build_search_params(geo_params: dict, row: SearchRow, scrape_cfg: dict, page_num: int) -> dict:
    # geo_params comes from the site's own SEOLandingPageCriteria (GeoIds where
    # available, PropertyTypeGroupID, PropertySearchTypeId, TransactionTypeId,
    # Sort, RecordsPerPage, Currency) - these are realtor.ca's own confirmed-
    # correct defaults for that city, so they take priority. Config only fills
    # in anything the site didn't provide.
    params = {k: v for k, v in geo_params.items() if not k.startswith("_")}
    params["view"] = "list"
    params.setdefault("Sort", scrape_cfg.get("sort", "6-D"))
    params.setdefault("PropertyTypeGroupID", scrape_cfg.get("property_type_group_id", 1))
    params.setdefault("TransactionTypeId", scrape_cfg.get("transaction_type_id", 2))
    params.setdefault("PropertySearchTypeId", 0)
    params.setdefault("Currency", scrape_cfg.get("currency", "CAD"))
    params["CurrentPage"] = page_num

    days_back = row.days_back if row.days_back is not None else scrape_cfg.get("default_days_back", 7)
    params["NumberOfDays"] = days_back

    if row.price_min:
        params["PriceMin"] = row.price_min
    if row.price_max:
        params["PriceMax"] = row.price_max

    return params


def build_search_url(geo_params: dict, row: SearchRow, scrape_cfg: dict, page_num: int) -> str:
    """Full standalone URL for a page - handy for logging/debugging; not used for navigation itself."""
    base_url = resolve_base_url(geo_params, row)
    params = build_search_params(geo_params, row, scrape_cfg, page_num)
    return f"{base_url}#{urlencode(params)}"


def _text(node) -> str:
    if not node:
        return ""
    return node.get_text(strip=True).replace("\xa0", " ")


def _extract_listing_id(href: str) -> str:
    match = re.search(r"/real-estate/(\d+)/", href)
    return match.group(1) if match else ""


def parse_listing_cards(html: str, region: str, city: str) -> List[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: List[Listing] = []

    for card in soup.select(CARD_SELECTOR):
        link = card.select_one("a.listingDetailsLink")
        if not link or not link.get("href"):
            continue
        href = link["href"]
        listing_url = href if href.startswith("http") else f"{BASE_URL}{href}"

        listing = Listing(
            region=region,
            city=city,
            listing_id=_extract_listing_id(href),
            listing_url=listing_url,
            mls_number=_text(card.select_one(".listingCardMLS span")),
            price=_text(card.select_one(".listingCardPrice")),
            full_address=_text(card.select_one(".listingCardAddress")),
            brokerage_name=_text(card.select_one(".listingCardOfficeName")),
        )

        img = card.select_one("img.listingCardImage")
        if img and img.get("src"):
            listing.image_url = img["src"]

        for icon in card.select(".listingCardIconCon"):
            label = _text(icon.select_one(".listingCardIconText")).lower()
            value = _text(icon.select_one(".listingCardIconNum"))
            if "bedroom" in label:
                listing.bedrooms = value
            elif "bathroom" in label:
                listing.bathrooms = value
            elif "square" in label:
                listing.square_footage = value

        tag_text = _text(card.select_one(".listingCardTagLabel"))
        if tag_text and RELATIVE_TIME_RE.search(tag_text):
            listing.listed_time_ago = tag_text

        listings.append(listing)

    return listings


def _wait_for_results_or_failure(driver, timeout_s: int = 25, poll_s: float = 0.5):
    """Poll until listing cards (or a "no results" marker) appear, or a guarded
    resource fails to load. Returns ("ready", None), ("blocked", (status, url)),
    or ("timeout", None)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        failures = get_network_failures(driver, GUARDED_RESOURCE_PATTERNS)
        if failures:
            return "blocked", failures[0]
        try:
            if driver.execute_script(RESULTS_READY_JS):
                return "ready", None
        except Exception:
            pass  # page mid-navigation; try again next poll
        time.sleep(poll_s)
    return "timeout", None


def _wait_for_page_change(driver, region: str, city: str, previous_signature: Tuple[str, ...], timeout_s: int = 25, poll_s: float = 0.5):
    """After clicking 'next page', poll until the rendered listings actually
    differ from `previous_signature` (not just "some card is present" - right
    after a click, the *previous* page's cards are still in the DOM until the
    AJAX response replaces them, so a naive presence check would return
    immediately without waiting for the real update)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        failures = get_network_failures(driver, GUARDED_RESOURCE_PATTERNS)
        if failures:
            return "blocked", failures[0]
        try:
            html = driver.page_source
        except Exception:
            html = ""
        listings = parse_listing_cards(html, region, city)
        if listings and _page_signature(listings) != previous_signature:
            return "ready", None
        if not listings and ("mapNoSidebarResultsCon" in html or "mapSideBarNoResults" in html):
            return "ready", None  # legitimately reached the end of results
        time.sleep(poll_s)
    return "timeout", None


def _land_on_search_page(driver, base_url: str, city: str, nav_retry_count: int) -> bool:
    """Do the one full page load per city, onto its plain SEO landing page (no hash).

    This has proven reliable throughout testing - it's the live filtered
    search that's fragile, not this page itself - so this uses a lighter
    retry than the per-page filter application below. Also gives the page's
    own JS a moment to finish initializing before we start changing its hash.
    """
    for attempt in range(1, nav_retry_count + 1):
        if attempt > 1:
            time.sleep(random.uniform(3, 6))
        try:
            driver.get(base_url)
        except Exception as exc:
            logger.warning(
                "Navigation error loading %s for %s (attempt %d/%d): %s", base_url, city, attempt, nav_retry_count, exc
            )
            continue

        if is_blocked_page(driver):
            logger.error("%s was blocked by realtor.ca's bot protection for %s.", base_url, city)
            return False

        time.sleep(2)  # let the page's own JS finish registering its hash-change listener
        return True

    return False


def _apply_search_filters(driver, params: dict, city: str, page_num: int, nav_retry_count: int) -> bool:
    """Apply a page's worth of search filters via an in-page hash change (no full reload).

    Returns True once the page has settled (listings or a "no results" marker
    present), False if it never did after all retries.
    """
    fragment = urlencode(params)

    for attempt in range(1, nav_retry_count + 1):
        if attempt > 1:
            time.sleep(random.uniform(3, 6))  # back off before retrying, don't hammer a slow/struggling server

        get_network_failures(driver, GUARDED_RESOURCE_PATTERNS)  # drain stale entries before this attempt

        try:
            driver.execute_script(SET_HASH_JS, fragment)
        except Exception as exc:
            logger.warning(
                "Error applying search filters on page %d for %s (attempt %d/%d): %s",
                page_num, city, attempt, nav_retry_count, exc,
            )
            continue

        outcome, detail = _wait_for_results_or_failure(driver)
        if outcome == "ready":
            return True
        if outcome == "blocked":
            status, failed_url = detail
            logger.error(
                "Page %d for %s never loaded results because a required script/check failed "
                "(HTTP %d on %s). This looks like realtor.ca blocking the search itself, not a "
                "network glitch - not retrying. Consider enabling a proxy in config.yaml, or trying "
                "again later from a less-flagged network.",
                page_num, city, status, failed_url,
            )
            return False
        logger.warning(
            "Timed out waiting for results on page %d for %s (attempt %d/%d).",
            page_num, city, attempt, nav_retry_count,
        )

    return False


def _click_next_page(driver) -> bool:
    """Click the visible 'next page' pagination control.

    There are normally several matching elements in the DOM (map sidebar,
    popup infobox, etc.) but only one is visible at a time - clicking a
    hidden one is a no-op. Returns False if none are visible (no next page /
    end of results).
    """
    try:
        candidates = driver.find_elements(By.CSS_SELECTOR, "a.lnkNextResultsPage")
    except Exception:
        return False

    for candidate in candidates:
        try:
            if candidate.is_displayed():
                driver.execute_script("arguments[0].click();", candidate)
                return True
        except Exception:
            continue
    return False


def _page_signature(listings: List[Listing]) -> Tuple[str, ...]:
    return tuple(listing.mls_number or listing.listing_id for listing in listings)


def fetch_all_pages(driver, geo_params: dict, row: SearchRow, scrape_cfg: dict, seen_mls: Optional[set] = None) -> List[Listing]:
    """Page through a city's search results (newest-listed first).

    Stops when any of:
      - a page comes back with no listings at all (end of results),
      - the "next page" control isn't there/visible any more (end of results),
      - a page's listings are identical to the previous page's - pagination
        has silently stalled and clicking isn't advancing it any further, or
      - `consecutive_seen_to_stop` listings in a row have already been scraped
        before (present in `seen_mls`). Because results are sorted newest
        first, a long unbroken run of already-seen listings means everything
        after it is old too, so there's no need to keep paging through the
        rest of this city's results.
    A single already-seen listing doesn't trigger the last case - re-listed/
    bumped listings can appear out of strict date order, so we wait for a
    real run of them before concluding we've caught up.
    """
    all_listings: List[Listing] = []
    seen_mls = seen_mls or set()
    max_pages = scrape_cfg.get("max_pages_per_search", 40)
    max_listings = scrape_cfg.get("max_listings_per_city", 600)
    delay_range = scrape_cfg.get("delay_between_pages_seconds", [3, 6])
    consecutive_seen_to_stop = scrape_cfg.get("consecutive_seen_to_stop", 20)
    nav_retry_count = scrape_cfg.get("nav_retry_count", 3)
    consecutive_seen = 0
    previous_signature: Optional[Tuple[str, ...]] = None

    base_url = resolve_base_url(geo_params, row)
    if not _land_on_search_page(driver, base_url, row.city, nav_retry_count):
        return all_listings

    # Page 1 sets the actual filters (price/date/sort/etc.) via a hash change.
    page1_params = build_search_params(geo_params, row, scrape_cfg, page_num=1)
    if not _apply_search_filters(driver, page1_params, row.city, 1, nav_retry_count):
        return all_listings

    for page_num in range(1, max_pages + 1):
        # Let async card rendering / lazy content settle.
        time.sleep(1.5)
        html = driver.page_source
        page_listings = parse_listing_cards(html, row.region, row.city)

        if not page_listings:
            logger.info("Page %d for %s returned no listings - stopping pagination.", page_num, row.city)
            break

        signature = _page_signature(page_listings)
        if signature == previous_signature:
            logger.info(
                "Page %d for %s repeated the previous page's listings - pagination has stalled, stopping.",
                page_num, row.city,
            )
            break
        previous_signature = signature

        all_listings.extend(page_listings)
        logger.info("Page %d for %s: %d listings (running total %d).", page_num, row.city, len(page_listings), len(all_listings))

        for listing in page_listings:
            key = listing.mls_number or f"id:{listing.listing_id}"
            if key in seen_mls:
                consecutive_seen += 1
                if consecutive_seen >= consecutive_seen_to_stop:
                    logger.info(
                        "%d already-scraped listings in a row for %s - assuming the rest are old too, "
                        "moving to the next city.",
                        consecutive_seen,
                        row.city,
                    )
                    return all_listings
            else:
                consecutive_seen = 0

        if len(all_listings) >= max_listings:
            logger.warning("Hit max_listings_per_city (%d) for %s - stopping early.", max_listings, row.city)
            break

        if page_num >= max_pages:
            break

        time.sleep(random.uniform(*delay_range))

        if not _click_next_page(driver):
            logger.info("No next-page control for %s - assuming end of results.", row.city)
            break

        outcome, detail = _wait_for_page_change(driver, row.region, row.city, previous_signature)
        if outcome == "blocked":
            status, failed_url = detail
            logger.error(
                "Page %d for %s never loaded after clicking next (HTTP %d on %s) - looks like "
                "realtor.ca blocking the search itself. Consider enabling a proxy in config.yaml.",
                page_num + 1, row.city, status, failed_url,
            )
            break
        if outcome == "timeout":
            logger.warning("Timed out waiting for page %d for %s after clicking next.", page_num + 1, row.city)
            break

    return all_listings
