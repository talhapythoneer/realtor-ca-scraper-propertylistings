"""Searches realtor.ca for a city's new listings through the site's own search API.

realtor.ca's map page gets its results from a JSON API
(api2.realtor.ca/Listing.svc/PropertySearch_Post) that takes the full set of
filters - area, price range, "listed in the last N days", sort, page size.
This scraper calls that same API from inside the browser page, so each
request goes out with the browser's own cookies, headers and TLS fingerprint,
exactly like the site's own requests.

Why not drive the site's UI instead (what the first version of this scraper
did)? It set the filters in the URL hash of each city's SEO landing page
(e.g. /bc/nanaimo/real-estate#PriceMin=...), but that page ignores the hash
entirely - confirmed live: 752 unfiltered Nanaimo listings before and after.
So no price or date filter was ever applied, and results were paged through
11 at a time until a "seen it before" heuristic or a 40-page cap stopped it.

The API only answers once the browser holds a `cf_api_tok` cookie, which
realtor.ca's map page sets when it loads. The token is short-lived (~30s,
measured), and calls made without a valid one fail CORS with no response. So
the session "primes" itself by (re)loading the map page whenever the token is
missing or about to expire, and re-primes and retries if a call still fails.
"""
import json
import logging
import random
import time
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

from .address import parse_address
from .browser import is_blocked_page, is_dead_browser_error
from .geocode import BBOX_KEYS, describe_area
from .models import Listing, SearchRow
from .numeric import clean_price

logger = logging.getLogger("realtor_scraper")

BASE_URL = "https://www.realtor.ca"
API_URL = "https://api2.realtor.ca/Listing.svc/PropertySearch_Post"
# Any map page works for priming - it's the page's own scripts that fetch the token.
PRIME_URL = f"{BASE_URL}/map#view=list&Sort=6-D&PropertyTypeGroupID=1&TransactionTypeId=2&PropertySearchTypeId=0&Currency=CAD"
API_TOKEN_COOKIE = "cf_api_tok"
TOKEN_SETTLE_S = 6   # see SearchSession.prime()
TOKEN_MARGIN_S = 5   # re-prime when the token has less than this left

# .NET DateTime ticks (100ns since 0001-01-01) - the format of InsertedDateUTC.
_TICKS_AT_UNIX_EPOCH = 621355968000000000

FETCH_JS = """
const done = arguments[arguments.length - 1];
fetch(arguments[0], {
    method: 'POST',
    credentials: 'include',
    headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
    body: new URLSearchParams(arguments[1]).toString(),
}).then(r => r.text().then(t => done({status: r.status, text: t})))
  .catch(e => done({status: -1, text: String(e)}));
"""


class SearchBlockedError(RuntimeError):
    """realtor.ca's search API kept refusing requests even after re-priming the session."""


class SearchSession:
    """Calls realtor.ca's search API from inside the browser."""

    def __init__(self, browser, config: dict):
        self.browser = browser  # BrowserSession - read .driver live, it changes on restart
        self.scrape_cfg = config["scrape"]
        self.retries = int(self.scrape_cfg.get("nav_retry_count", 3))
        self.delay_range = self.scrape_cfg.get("delay_between_api_calls_seconds", [1, 2])
        self._primed = False

    @property
    def driver(self):
        return self.browser.driver

    def reset(self):
        """Forget the primed state - call after the browser is restarted."""
        self._primed = False

    def _api_token(self) -> Optional[dict]:
        try:
            cookies = self.driver.execute_cdp_cmd("Network.getAllCookies", {})["cookies"]
        except Exception as exc:
            if is_dead_browser_error(exc):
                raise
            return None
        return next((c for c in cookies if c.get("name") == API_TOKEN_COOKIE), None)

    def _token_fresh(self, margin_s: float = TOKEN_MARGIN_S) -> bool:
        token = self._api_token()
        if not token:
            return False
        expires = token.get("expires") or -1
        return expires <= 0 or expires > time.time() + margin_s  # <= 0: session cookie, no expiry

    def prime(self, timeout_s: float = 30):
        """(Re)load realtor.ca's map page and wait for a usable API token."""
        logger.debug("Opening realtor.ca's map page for a fresh search token...")
        try:
            # Via a blank page: if the browser is already on PRIME_URL, get() of the
            # same URL is a no-op, the page's scripts don't run again, and no fresh
            # token is fetched - seen in testing as three failed retries in a row.
            self.driver.get("about:blank")
            self.driver.get(PRIME_URL)
        except Exception as exc:
            if is_dead_browser_error(exc):
                raise
            # A page-load timeout here is usually just slow ads/trackers; the page's
            # own scripts may well have run anyway, which is all that matters.
            logger.debug("Map page load did not complete cleanly: %s", exc)

        if is_blocked_page(self.driver):
            raise SearchBlockedError(
                "realtor.ca's bot protection blocked the map page. Try again later, "
                "or from a different network / with a proxy (see README)."
            )

        # Measured: the page sets a first token on load and replaces it 2-5s later;
        # requests made with the first one can be refused. So wait for the
        # replacement (or TOKEN_SETTLE_S, whichever comes first).
        deadline = time.time() + timeout_s
        first_value, first_seen = None, None
        while time.time() < deadline:
            token = self._api_token()
            if token:
                if first_value is None:
                    first_value, first_seen = token.get("value"), time.time()
                elif token.get("value") != first_value or time.time() - first_seen >= TOKEN_SETTLE_S:
                    if self._token_fresh():
                        self._primed = True
                        return
            time.sleep(0.5)
        logger.warning("realtor.ca's map page didn't provide a search token within %.0fs.", timeout_s)
        self._primed = True  # still worth trying - the request itself is the real test

    def _post(self, body: dict) -> Tuple[int, str]:
        self.driver.set_script_timeout(60)
        result = self.driver.execute_async_script(FETCH_JS, API_URL, {k: str(v) for k, v in body.items()})
        return result.get("status", -1), result.get("text", "")

    def query(self, body: dict) -> dict:
        """POST one search request; re-primes the session and retries on failure."""
        last_problem = ""
        for attempt in range(1, self.retries + 1):
            if attempt > 1:
                time.sleep(random.uniform(3, 6))
                self.prime()
            elif not self._primed or not self._token_fresh():
                # The token only lasts ~30s (measured); a request sent with an expired
                # one is refused. Cheaper to check than to fail and retry.
                self.prime()
            try:
                status, text = self._post(body)
            except Exception as exc:
                if is_dead_browser_error(exc):
                    raise
                status, text = -1, str(exc)
            if status == 200:
                try:
                    data = json.loads(text)
                except ValueError:
                    last_problem = f"unreadable response: {text[:120]!r}"
                else:
                    if data.get("ErrorCode", {}).get("Id", 200) == 200 or "Results" in data:
                        return data
                    last_problem = f"API error {data.get('ErrorCode')}"
            else:
                last_problem = f"HTTP {status}" if status != -1 else "request refused (no API token / blocked)"
            logger.warning("Search request failed (attempt %d/%d): %s", attempt, self.retries, last_problem)
        raise SearchBlockedError(f"realtor.ca's search kept failing after {self.retries} attempts: {last_problem}")

    def pause(self):
        time.sleep(random.uniform(*self.delay_range))


# -- result parsing ---------------------------------------------------------------------


def _ticks_to_local_date(ticks: str, gmt_offset: str) -> str:
    """InsertedDateUTC ("639269335056670000") -> listing-local YYYY-MM-DD.
    gmt_offset is realtor.ca's ListingGMT, e.g. "-08:00:00"."""
    try:
        moment = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            microseconds=(int(ticks) - _TICKS_AT_UNIX_EPOCH) // 10
        )
    except (TypeError, ValueError):
        return ""
    try:
        sign = -1 if gmt_offset.strip().startswith("-") else 1
        hours, minutes = gmt_offset.strip().lstrip("+-").split(":")[:2]
        moment += sign * timedelta(hours=int(hours), minutes=int(minutes))
    except (AttributeError, ValueError):
        pass
    return moment.date().isoformat()


def _phone(phones: list, phone_type: str) -> str:
    for phone in phones or []:
        if (phone.get("PhoneType") or "").lower() == phone_type.lower():
            area, number = phone.get("AreaCode", ""), phone.get("PhoneNumber", "")
            return f"{area}-{number}" if area else number
    return ""


def listing_from_result(result: dict, region: str, city: str) -> Listing:
    prop = result.get("Property") or {}
    building = result.get("Building") or {}
    address = prop.get("Address") or {}
    agents = result.get("Individual") or [{}]
    agent = agents[0] or {}
    office = agent.get("Organization") or {}
    photos = prop.get("Photo") or [{}]

    full_address = " ".join((address.get("AddressText") or "").replace("|", ", ").split())
    parsed = parse_address(full_address)
    listing = Listing(
        region=region,
        city=city,
        mls_number=result.get("MlsNumber", ""),
        listing_id=str(result.get("Id", "")),
        price=prop.get("PriceUnformattedValue") or prop.get("Price", ""),
        unit=parsed["unit"],
        street_address=parsed["street_address"],
        address_city=parsed["address_city"],
        full_address=full_address,
        postal_code=result.get("PostalCode") or parsed["postal_code"],
        province=result.get("ProvinceName") or parsed["province"],
        property_type=prop.get("Type", ""),
        building_type=building.get("Type", ""),
        bedrooms=building.get("Bedrooms", ""),
        bathrooms=building.get("BathroomTotal", ""),
        square_footage=building.get("SizeInterior", ""),
        storeys=building.get("StoriesTotal", ""),
        listed_time_ago=result.get("TimeOnRealtor", ""),
        estimated_listed_date=_ticks_to_local_date(result.get("InsertedDateUTC"), result.get("ListingGMT", "")),
        listing_url=f"{BASE_URL}{result.get('RelativeDetailsURL', '')}" if result.get("RelativeDetailsURL") else "",
        image_url=photos[0].get("MedResPath", "") if photos and photos[0] else "",
        agent_name=agent.get("Name", ""),
        agent_phone=_phone(agent.get("Phones"), "Telephone"),
        brokerage_name=office.get("Name", ""),
        brokerage_phone=_phone(office.get("Phones"), "Telephone"),
        brokerage_fax=_phone(office.get("Phones"), "Fax"),
        brokerage_address=" ".join(((office.get("Address") or {}).get("AddressText") or "").replace("|", ", ").split()),
        description=result.get("PublicRemarks", ""),
    )
    return listing


# -- searching --------------------------------------------------------------------------


def days_back_for(row: SearchRow, scrape_cfg: dict) -> int:
    return row.days_back if row.days_back is not None else int(scrape_cfg.get("default_days_back", 7))


def build_search_body(geo_params: dict, row: SearchRow, scrape_cfg: dict,
                      price_min: Optional[int], price_max: Optional[int], page: int) -> dict:
    body = {
        "Sort": scrape_cfg.get("sort", "6-D"),
        "PropertyTypeGroupID": scrape_cfg.get("property_type_group_id", 1),
        "TransactionTypeId": scrape_cfg.get("transaction_type_id", 2),
        "PropertySearchTypeId": scrape_cfg.get("property_search_type_id", 0),
        "Currency": scrape_cfg.get("currency", "CAD"),
        "IncludeHiddenListings": "false",
        "NumberOfDays": days_back_for(row, scrape_cfg),
        "RecordsPerPage": scrape_cfg.get("api_records_per_page", 200),
        "CurrentPage": page,
        "ApplicationId": 1,
        "CultureId": 1,
        "Version": "7.0",
    }
    if geo_params.get("GeoIds"):
        body["GeoIds"] = geo_params["GeoIds"]
    else:
        body.update({k: geo_params[k] for k in BBOX_KEYS})
        body["ZoomLevel"] = 13
    if price_min:
        body["PriceMin"] = price_min
    if price_max:
        body["PriceMax"] = price_max
    return body


def _search_price_band(session: SearchSession, geo_params: dict, row: SearchRow, scrape_cfg: dict,
                       price_min: Optional[int], price_max: Optional[int], depth: int = 0) -> List[dict]:
    """All results for one price band, paging through the API. realtor.ca stops
    returning results past MaxRecords (600) for a single search, so a band with
    more than that is split in two and each half searched separately."""
    first = session.query(build_search_body(geo_params, row, scrape_cfg, price_min, price_max, 1))
    paging = first.get("Paging") or {}
    total = int(paging.get("TotalRecords") or 0)
    max_records = int(paging.get("MaxRecords") or 600)

    if total > max_records and depth < 8:
        low = price_min or 0
        high = price_max or max(low * 2, low + 1_000_000)
        mid = (low + high) // 2
        logger.info(
            "%s: %d listings in $%s-%s is over realtor.ca's %d-per-search limit - splitting the price range.",
            row.city, total, f"{low:,}", f"{price_max:,}" if price_max else "max", max_records,
        )
        session.pause()
        lower = _search_price_band(session, geo_params, row, scrape_cfg, price_min, mid, depth + 1)
        session.pause()
        upper = _search_price_band(session, geo_params, row, scrape_cfg, mid + 1, price_max, depth + 1)
        return lower + upper
    if total > max_records:
        logger.warning("%s: still %d listings after splitting the price range - only the first %d are read.",
                       row.city, total, max_records)

    results = list(first.get("Results") or [])
    total_pages = int(paging.get("TotalPages") or 1)
    for page in range(2, total_pages + 1):
        session.pause()
        data = session.query(build_search_body(geo_params, row, scrape_cfg, price_min, price_max, page))
        results.extend(data.get("Results") or [])
    return results


def _outside_filters(listing: Listing, row: SearchRow, cutoff: date) -> str:
    """Why a listing doesn't match the row's filters (or "" if it does). A safety net
    so a filter that realtor.ca stops honouring can never silently leak into the output."""
    price = clean_price(listing.price)
    if price is not None:
        if row.price_min and price < row.price_min:
            return "below price_min"
        if row.price_max and price > row.price_max:
            return "above price_max"
    if listing.estimated_listed_date and listing.estimated_listed_date < cutoff.isoformat():
        return "older than days_back"
    return ""


def search_city(session: SearchSession, geo_params: dict, row: SearchRow, scrape_cfg: dict) -> List[Listing]:
    """Every listing in a row's area, price range and listed-since window."""
    days_back = days_back_for(row, scrape_cfg)
    logger.info(
        "%s: searching %s, $%s-%s, listed in the last %d day(s).",
        row.city, describe_area(geo_params), f"{row.price_min:,}" if row.price_min else "0",
        f"{row.price_max:,}" if row.price_max else "max", days_back,
    )
    raw_results = _search_price_band(session, geo_params, row, scrape_cfg, row.price_min, row.price_max)

    # One day of slack: realtor.ca counts the window in its own timezone, and
    # InsertedDateUTC -> local date can land a listing just across midnight.
    cutoff = (datetime.now() - timedelta(days=days_back + 1)).date()
    listings, seen, dropped = [], set(), {}
    for result in raw_results:
        listing = listing_from_result(result, row.region, row.city)
        key = listing.mls_number or listing.listing_id
        if key in seen:
            continue
        seen.add(key)
        reason = _outside_filters(listing, row, cutoff)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        listings.append(listing)

    if dropped:
        logger.warning(
            "%s: realtor.ca returned %d listing(s) outside this row's filters (%s) - dropped them.",
            row.city, sum(dropped.values()), ", ".join(f"{n} {why}" for why, n in dropped.items()),
        )
    return listings
