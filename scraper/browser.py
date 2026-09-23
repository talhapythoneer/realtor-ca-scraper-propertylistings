"""undetected_chromedriver browser setup, network-status helpers, and the
(currently disabled) proxy hook.

realtor.ca's bot protection blocks a live search from executing under
Playwright - even driving the real installed Chrome binary with fingerprint
overrides applied - via a specific script (bundles/js/desktop/page/listing)
and a behavioural /ping.html check that both fail under Playwright but pass
under undetected_chromedriver in non-headless mode. This was confirmed
directly in testing: the same bundle that consistently 403/503'd under
Playwright returned 200 under undetected_chromedriver, and searches that
never rendered results under Playwright returned real listings within ~1
second here.

Headless mode gets blocked immediately, even on a plain page load - also
confirmed directly in testing. Keep `scrape.headless: false` in config.yaml
for real runs; this is a hard requirement of this specific site's protection,
not just a suggestion.
"""
import json
import logging
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkdtemp
from typing import List, Tuple
from urllib.parse import urlparse
import chrome_version

# Fetch the local Chrome version
current_version = chrome_version.get_chrome_version().split('.')[0]
current_version = int(current_version)

print({
    'current_version': current_version
})

import undetected_chromedriver as uc

logger = logging.getLogger("realtor_scraper")

BLOCKED_TITLE_MARKERS = ("you have been blocked", "vous avez été bloqué")


def is_blocked_page(driver) -> bool:
    title = (driver.title or "").lower()
    return any(marker in title for marker in BLOCKED_TITLE_MARKERS)


def wait_for_css(driver, selector: str, timeout_s: float = 20, poll_s: float = 0.5) -> bool:
    """Poll until an element matching `selector` exists in the DOM."""
    script = f"return document.querySelector({selector!r}) !== null;"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if driver.execute_script(script):
                return True
        except Exception:
            pass  # page mid-navigation; try again next poll
        time.sleep(poll_s)
    return False


def get_network_failures(driver, url_patterns: Tuple[str, ...]) -> List[Tuple[int, str]]:
    """Drain the CDP performance log and return (status, url) for responses matching
    one of `url_patterns` with an error status. Draining removes entries, so each
    call only sees what's happened since the last call - poll regularly."""
    failures = []
    try:
        entries = driver.get_log("performance")
    except Exception:
        return failures

    for entry in entries:
        try:
            message = json.loads(entry["message"])["message"]
        except (KeyError, ValueError):
            continue
        if message.get("method") != "Network.responseReceived":
            continue
        response = message.get("params", {}).get("response", {})
        url = response.get("url", "")
        status = response.get("status", 0)
        if status >= 400 and any(pattern in url for pattern in url_patterns):
            failures.append((status, url))
    return failures


def _build_proxy_auth_extension(username: str, password: str) -> Path:
    """Build a temporary unpacked Chrome extension that silently answers proxy auth
    prompts. Chrome does not accept embedded credentials in --proxy-server, so an
    authenticated proxy needs this instead. Untested against a real proxy (this
    project shipped without one) - verify this works once a proxy is added."""
    ext_dir = Path(mkdtemp(prefix="realtor_proxy_auth_"))
    manifest = {
        "manifest_version": 2,
        "name": "Proxy Auth",
        "version": "1.0",
        "permissions": ["proxy", "webRequest", "webRequestBlocking", "<all_urls>"],
        "background": {"scripts": ["background.js"]},
    }
    background_js = (
        "chrome.webRequest.onAuthRequired.addListener("
        "function(details) { return {authCredentials: {username: %r, password: %r}}; },"
        '{urls: ["<all_urls>"]}, ["blocking"]);' % (username, password)
    )
    (ext_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (ext_dir / "background.js").write_text(background_js, encoding="utf-8")
    return ext_dir


@contextmanager
def launch_browser(config: dict):
    scrape_cfg = config["scrape"]
    proxy_cfg = config["proxy"]

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1440,900")
    if scrape_cfg.get("user_agent"):
        options.add_argument(f"--user-agent={scrape_cfg['user_agent']}")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    proxy_extension_dir = None
    if proxy_cfg.get("enabled"):
        server = proxy_cfg.get("server")
        if not server:
            raise ValueError("proxy.enabled is true in config.yaml but proxy.server is empty.")
        parsed = urlparse(server)
        proxy_server_arg = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}" if parsed.hostname else server
        if proxy_cfg.get("username") and proxy_cfg.get("password"):
            proxy_extension_dir = _build_proxy_auth_extension(proxy_cfg["username"], proxy_cfg["password"])
            options.add_argument(f"--load-extension={proxy_extension_dir}")
        options.add_argument(f"--proxy-server={proxy_server_arg}")
        logger.info("Launching browser with proxy %s", proxy_server_arg)

    headless = bool(scrape_cfg.get("headless", False))
    if headless:
        logger.warning(
            "scrape.headless is true - realtor.ca's bot protection was confirmed in testing to block "
            "even the very first page load in headless mode under this browser. Strongly recommend "
            "setting headless: false in config.yaml for real runs."
        )

    driver = uc.Chrome(options=options, headless=headless, version_main=current_version)
    page_load_timeout_s = scrape_cfg.get("page_load_timeout_ms", 45000) / 1000
    driver.set_page_load_timeout(page_load_timeout_s)

    try:
        yield driver
    finally:
        driver.quit()
        if proxy_extension_dir:
            shutil.rmtree(proxy_extension_dir, ignore_errors=True)
