"""Fetches listing detail pages over plain HTTP instead of the browser.

The browser is still what gets past realtor.ca's bot protection (Cloudflare) -
it runs the site's JS challenge and ends up holding valid session cookies
(cf_clearance, __cf_bm, ...). Once it has them, individual listing pages are
ordinary server-rendered HTML, so they can be fetched much faster by copying
the browser's cookies and request headers into plain HTTP sessions, rather than
driving the browser to each page and waiting for it to render.

The HTTP client is curl_cffi (a requests-compatible API over curl-impersonate),
not httpx/requests: confirmed in testing that the site rejects httpx with a 403
even when it carries every one of the browser's cookies and headers - it
fingerprints the TLS/HTTP2 handshake itself, which Python's ssl module can't
make look like Chrome. curl_cffi reproduces Chrome's handshake, and with the
browser's cookies returned 200s with full listing pages. (Without the
browser's cookies it gets a 403 too - both halves are needed.)

Cloudflare also rate-limits listing pages: confirmed in testing that ~60
requests inside a minute gets every further request answered with a 429
"Security Check" challenge (header `cf-mitigated: challenge`), which lifts on
its own after roughly a minute. Re-copying the browser's cookies does NOT lift
it early. The browser itself kept loading listings fine throughout. So:
  - all HTTP requests share one pacing clock (http_max_requests_per_minute),
    however many worker threads there are, and
  - on a challenge, HTTP pauses for http_cooldown_seconds while the browser
    keeps working through the queue, then HTTP resumes with fresh cookies.

The same IP and user agent must be used as the browser's, since the session
cookies are bound to them - so the configured proxy (if any) is applied here
too, and the user agent is read from the live browser rather than configured.
"""
import logging
import queue
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional
from urllib.parse import quote, urlparse

from curl_cffi import requests as curl_requests

from .browser import wait_for_css
from .detail import enrich_listing_with_detail_page
from .models import Listing

logger = logging.getLogger("realtor_scraper")

# Present on every real listing page; absent from challenge/block pages.
DETAIL_PAGE_MARKER = 'id="listingAddress"'

# realtor.ca listings have 404'd/redirected here when delisted - worth a browser
# retry anyway, but not evidence the HTTP session is being blocked.
NOT_BLOCKED_STATUSES = (404, 410)

BROWSER_INFO_JS = """
const uaData = navigator.userAgentData;
return {
    userAgent: navigator.userAgent,
    languages: navigator.languages || [navigator.language],
    brands: uaData ? uaData.brands : null,
    mobile: uaData ? uaData.mobile : false,
    platform: uaData ? uaData.platform : null,
};
"""


def _accept_language(languages: List[str]) -> str:
    # Mirrors how Chrome builds its own header from navigator.languages.
    parts = []
    for i, lang in enumerate(languages or ["en-US", "en"]):
        q = max(0.1, round(1 - i * 0.1, 1))
        parts.append(lang if i == 0 else f"{lang};q={q}")
    return ",".join(parts)


def _build_proxy_url(proxy_cfg: dict) -> Optional[str]:
    if not proxy_cfg.get("enabled") or not proxy_cfg.get("server"):
        return None
    server = proxy_cfg["server"]
    username, password = proxy_cfg.get("username"), proxy_cfg.get("password")
    if not (username and password):
        return server
    parsed = urlparse(server)
    return f"{parsed.scheme}://{quote(username, safe='')}:{quote(password, safe='')}@{parsed.hostname}:{parsed.port}"


class DetailFetcher:
    def __init__(self, driver, config: dict):
        scrape_cfg = config["scrape"]
        self.driver = driver
        self.method = scrape_cfg.get("detail_fetch_method", "http")
        self.concurrency = max(1, int(scrape_cfg.get("http_concurrency", 2)))
        self.request_interval_s = 60.0 / float(scrape_cfg.get("http_max_requests_per_minute", 50))
        self.cooldown_s = float(scrape_cfg.get("http_cooldown_seconds", 90))
        self.browser_delay_range = scrape_cfg.get("delay_between_listings_seconds", [1, 1])
        self.max_consecutive_failures = int(scrape_cfg.get("http_max_consecutive_failures", 5))
        self.impersonate = scrape_cfg.get("http_impersonate", "chrome")
        self.timeout_s = float(scrape_cfg.get("http_timeout_seconds", 30))
        self.proxy_url = _build_proxy_url(config["proxy"])

        # Filled in by sync_from_browser(); every HTTP session is built from these.
        self._headers: dict = {}
        self._cookies: List[dict] = []

        self._lock = threading.Lock()
        self._next_request_at = 0.0  # shared pacing clock - persists across cities
        self._consecutive_failures = 0
        self._tripped = threading.Event()  # stop issuing HTTP requests for this batch
        self._challenged = threading.Event()  # ...because Cloudflare's rate limit kicked in

        # Progress logging for the batch enrich() is currently working through - reset
        # at the top of each enrich() call. Only one enrich() runs at a time (rows are
        # processed one after another), so plain instance attributes are fine here.
        self._progress_lock = threading.Lock()
        self._progress_done = 0
        self._progress_total = 0
        self._progress_label = ""
        self._progress_started = 0.0

    def _new_session(self) -> curl_requests.Session:
        session = curl_requests.Session(
            impersonate=self.impersonate, timeout=self.timeout_s, proxy=self.proxy_url, allow_redirects=True
        )
        session.headers.update(self._headers)
        for cookie in self._cookies:
            session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie.get("path", "/"))
        return session

    # -- browser -> HTTP session copy --------------------------------------------------

    def sync_from_browser(self):
        """Snapshot the browser's current cookies and request headers for the HTTP sessions."""
        info = self.driver.execute_script(BROWSER_INFO_JS)
        headers = {
            "User-Agent": info["userAgent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
            "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": _accept_language(info.get("languages")),
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        }
        if info.get("brands"):
            headers["sec-ch-ua"] = ", ".join(f'"{b["brand"]}";v="{b["version"]}"' for b in info["brands"])
            headers["sec-ch-ua-mobile"] = "?1" if info.get("mobile") else "?0"
            headers["sec-ch-ua-platform"] = f'"{info.get("platform") or "Windows"}"'
        try:
            headers["Referer"] = self.driver.current_url.split("#")[0]
        except Exception:
            pass

        # CDP sees every cookie (incl. HttpOnly ones on other realtor.ca subdomains);
        # get_cookies() only sees the current page's domain, so it's just the fallback.
        try:
            cookies = self.driver.execute_cdp_cmd("Network.getAllCookies", {})["cookies"]
        except Exception:
            cookies = self.driver.get_cookies()

        self._headers = headers
        self._cookies = [c for c in cookies if "realtor.ca" in c.get("domain", "")]

        with self._lock:
            self._consecutive_failures = 0
        self._tripped.clear()
        self._challenged.clear()
        logger.debug("Copied %d cookie(s) and headers from the browser for HTTP requests.", len(self._cookies))

    # -- fetching ----------------------------------------------------------------------

    def _wait_for_slot(self):
        """Block until this thread may send its next request under the shared rate limit.

        Slots are handed out one interval apart (jittered +-15%, so the average
        still matches the configured rate) across every worker thread.
        """
        with self._lock:
            now = time.time()
            slot = max(self._next_request_at, now)
            self._next_request_at = slot + self.request_interval_s * random.uniform(0.85, 1.15)
        if slot > now:
            time.sleep(slot - now)

    def _record(self, ok: bool):
        with self._lock:
            if ok:
                self._consecutive_failures = 0
                return
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.max_consecutive_failures and not self._tripped.is_set():
                logger.warning(
                    "%d HTTP detail requests in a row failed - switching to the browser for the rest of this city.",
                    self._consecutive_failures,
                )
                self._tripped.set()

    def _record_challenge(self, status: int):
        with self._lock:
            if self._challenged.is_set():
                return
            logger.warning(
                "HTTP %d security challenge from realtor.ca (rate limit) - pausing HTTP requests for %.0fs.",
                status, self.cooldown_s,
            )
            self._challenged.set()
            self._tripped.set()

    def _note_progress(self, via: str, listing: Listing):
        """Log one line per listing as it's finished, so a run never goes quiet for the
        length of a whole batch - visible proof the scraper is still working, not stuck."""
        with self._progress_lock:
            self._progress_done += 1
            done, total = self._progress_done, self._progress_total
        elapsed = time.time() - self._progress_started
        identifier = listing.mls_number or listing.listing_id or listing.listing_url
        logger.info(
            "%s: %d/%d detail page(s) done (%s, %.0fs elapsed) - %s",
            self._progress_label, done, total, via, elapsed, identifier,
        )

    def _fetch_one_http(self, sessions: queue.Queue, listing: Listing) -> bool:
        if self._tripped.is_set():
            return False
        self._wait_for_slot()
        if self._tripped.is_set():
            return False

        # curl_cffi sessions aren't thread-safe, so each request borrows one from the pool.
        session = sessions.get()
        try:
            response = session.get(listing.listing_url)
        except Exception as exc:
            logger.debug("HTTP error fetching %s: %s", listing.listing_url, exc)
            self._record(False)
            return False
        finally:
            sessions.put(session)

        html = response.text
        if response.status_code == 200 and DETAIL_PAGE_MARKER in html:
            try:
                enrich_listing_with_detail_page(listing, html)
            except Exception as exc:
                logger.warning("Could not parse listing detail page %s: %s", listing.listing_url, exc)
            self._record(True)
            self._note_progress("http", listing)
            return True

        logger.debug("HTTP %d / no listing content for %s", response.status_code, listing.listing_url)
        if response.headers.get("cf-mitigated") or response.status_code in (403, 429):
            self._record_challenge(response.status_code)
        elif response.status_code not in NOT_BLOCKED_STATUSES:
            self._record(False)
        return False

    def _fetch_batch_http(self, listings: List[Listing]) -> List[Listing]:
        """Fetch listings concurrently over HTTP; returns the ones that failed or were skipped."""
        sessions: queue.Queue = queue.Queue()
        for _ in range(min(self.concurrency, len(listings))):
            sessions.put(self._new_session())
        try:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                results = list(pool.map(lambda listing: self._fetch_one_http(sessions, listing), listings))
        finally:
            while not sessions.empty():
                sessions.get().close()
        return [listing for listing, ok in zip(listings, results) if not ok]

    def _fetch_one_browser(self, listing: Listing):
        try:
            self.driver.get(listing.listing_url)
            wait_for_css(self.driver, "#listingAddress", timeout_s=20)
            time.sleep(random.uniform(*self.browser_delay_range))
            enrich_listing_with_detail_page(listing, self.driver.page_source)
        except Exception as exc:
            logger.warning("Could not load listing detail page %s: %s", listing.listing_url, exc)
        self._note_progress("browser", listing)

    def enrich(self, listings: List[Listing], label: str = ""):
        """Fill in detail-page fields for every listing that has a URL, in place."""
        pending = [listing for listing in listings if listing.listing_url]
        if not pending:
            return

        started = time.time()
        total = len(pending)
        self._progress_done = 0
        self._progress_total = total
        self._progress_label = label
        self._progress_started = started
        logger.info("%s: fetching %d detail page(s)...", label, total)

        if self.method != "http":
            for listing in pending:
                self._fetch_one_browser(listing)
            return

        via_browser = 0
        fruitless_rounds = 0
        self.sync_from_browser()

        while pending:
            attempted = len(pending)
            pending = self._fetch_batch_http(pending)
            if not pending or not self._challenged.is_set():
                break  # done, or failures that waiting out a rate limit won't fix

            # Rate-limited: HTTP stays challenged for about a minute whatever we do,
            # but the browser is unaffected - keep it working through the queue until
            # the cooldown is over, then resume HTTP with its fresh cookies.
            fruitless_rounds = fruitless_rounds + 1 if len(pending) == attempted else 0
            if fruitless_rounds >= 2:
                logger.warning("%s: HTTP still challenged after cooling down - using the browser instead.", label)
                break
            resume_at = time.time() + self.cooldown_s
            while pending and time.time() < resume_at:
                self._fetch_one_browser(pending.pop(0))
                via_browser += 1
            if pending:
                logger.info("%s: resuming HTTP for the remaining %d detail page(s).", label, len(pending))
                self.sync_from_browser()

        if pending:
            logger.warning("%s: loading %d detail page(s) in the browser instead.", label, len(pending))
            for listing in pending:
                self._fetch_one_browser(listing)
            via_browser += len(pending)

        logger.info(
            "%s: fetched %d detail page(s) in %.1fs (%d via browser).",
            label, total, time.time() - started, via_browser,
        )
