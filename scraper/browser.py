"""undetected_chromedriver browser setup, crash detection, and the
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
from pathlib import Path
from tempfile import mkdtemp
from typing import Optional
from urllib.parse import urlparse

import chrome_version
import undetected_chromedriver as uc
from selenium.common.exceptions import InvalidSessionIdException, NoSuchWindowException

logger = logging.getLogger("realtor_scraper")

BLOCKED_TITLE_MARKERS = ("you have been blocked", "vous avez été bloqué")


# Error text Selenium/chromedriver produce once Chrome itself has crashed or its
# window was closed. Seen in real runs: after this, every later call fails the
# same way, so the browser has to be relaunched rather than the call retried.
DEAD_BROWSER_MARKERS = (
    "invalid session id",
    "no such window",
    "target window already closed",
    "chrome not reachable",
    "session deleted",
    "disconnected: not connected to devtools",
    "unable to receive message from renderer",
    "max retries exceeded",
    "connection refused",
)


def is_dead_browser_error(exc: BaseException) -> bool:
    if isinstance(exc, (InvalidSessionIdException, NoSuchWindowException)):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in DEAD_BROWSER_MARKERS)


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


def _installed_chrome_major_version() -> Optional[int]:
    """Major version of the installed Chrome, so undetected_chromedriver downloads a
    matching driver (its own detection picked the wrong one on some machines).
    None lets undetected_chromedriver detect it itself."""
    try:
        return int(chrome_version.get_chrome_version().split(".")[0])
    except Exception:
        return None


class BrowserSession:
    """Owns the Chrome instance for a run, and can relaunch it if it dies mid-run.

    Use as a context manager; always read the live driver from `.driver`, since
    restart() replaces it.
    """

    def __init__(self, config: dict):
        self.config = config
        self.driver = None
        self._proxy_extension_dir: Optional[Path] = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc_info):
        self.quit()

    def start(self):
        scrape_cfg = self.config["scrape"]
        proxy_cfg = self.config["proxy"]

        options = uc.ChromeOptions()
        options.add_argument("--window-size=1440,900")
        # "eager" returns once the DOM is ready instead of waiting for every ad/tracker
        # on the page to finish - with "normal", realtor.ca page loads regularly ran past
        # the 45s timeout ("Timed out receiving message from renderer") even though the
        # page itself was usable, which cost whole cities in real runs.
        options.page_load_strategy = scrape_cfg.get("page_load_strategy", "eager")
        if scrape_cfg.get("user_agent"):
            options.add_argument(f"--user-agent={scrape_cfg['user_agent']}")

        if proxy_cfg.get("enabled"):
            server = proxy_cfg.get("server")
            if not server:
                raise ValueError("proxy.enabled is true in config.yaml but proxy.server is empty.")
            parsed = urlparse(server)
            proxy_server_arg = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}" if parsed.hostname else server
            if proxy_cfg.get("username") and proxy_cfg.get("password"):
                self._proxy_extension_dir = _build_proxy_auth_extension(proxy_cfg["username"], proxy_cfg["password"])
                options.add_argument(f"--load-extension={self._proxy_extension_dir}")
            options.add_argument(f"--proxy-server={proxy_server_arg}")
            logger.info("Launching browser with proxy %s", proxy_server_arg)

        headless = bool(scrape_cfg.get("headless", False))
        if headless:
            logger.warning(
                "scrape.headless is true - realtor.ca's bot protection was confirmed in testing to block "
                "even the very first page load in headless mode under this browser. Strongly recommend "
                "setting headless: false in config.yaml for real runs."
            )

        version_main = _installed_chrome_major_version()
        logger.debug("Launching Chrome (installed major version: %s).", version_main)
        self.driver = uc.Chrome(options=options, headless=headless, version_main=version_main)
        self.driver.set_page_load_timeout(scrape_cfg.get("page_load_timeout_ms", 45000) / 1000)

    def quit(self):
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass  # already dead - nothing left to close
            self.driver = None
        if self._proxy_extension_dir:
            shutil.rmtree(self._proxy_extension_dir, ignore_errors=True)
            self._proxy_extension_dir = None

    def restart(self):
        logger.warning("Restarting the browser...")
        self.quit()
        time.sleep(3)
        self.start()
