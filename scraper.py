# DISCLAIMER: Toda documentacion fue realizada en ingles por motivos de universalidad.

"""
Cambodia IP Portal - Trademark Scraper
=======================================
Scrapes trademark data from https://digitalip.cambodiaip.gov.kh/en/trademark-search

Architecture (hybrid - browser minimized):
- Playwright (headless, shared): Session initialization + detail page rendering
- httpx (async): All API search calls + image downloads

The SPA exposes a REST API at /api/v1/web/trademark-search that we call directly
after obtaining session cookies via a single browser page load. Images require the
laravel_session cookie, which we carry in the httpx client.
"""

import asyncio
import json
import logging
import re
import urllib.parse
from pathlib import Path

import httpx
from playwright.async_api import async_playwright, Page, BrowserContext
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_log,
    after_log,
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
log = logging.getLogger("cambodia_scraper")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL = "https://digitalip.cambodiaip.gov.kh"
SEARCH_URL = f"{BASE_URL}/en/trademark-search"
SEARCH_API = f"{BASE_URL}/api/v1/web/trademark-search"
IMAGE_URL = f"{BASE_URL}/trademark-logo/{{trademark_id}}?type=ts_logo_thumbnail"
OUTPUT_DIR = Path("output")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

FILING_NUMBERS = [
    "KH/49633/12",   # MINGFAI  - Ming Fai Enterprise International Co., Ltd.
    "KH/59286/14",   # Eesatto  - DIAMOND POINT Sdn Bhd
    "KH/83498/19",   # FORCE    - TIFORCE INTERNATIONAL CO., LTD.
]

# Max retries for network operations
MAX_RETRIES = 3
PAGE_TIMEOUT_MS = 30_000
NETWORK_IDLE_TIMEOUT_MS = 10_000


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def filing_number_to_filename(filing_number: str) -> str:
    """
    Convert 'KH/49633/12' -> 'KH4963312'
    Removes all '/' characters as per the naming convention.
    """
    return filing_number.replace("/", "")


def build_search_payload(filing_number: str, page: int = 1, per_page: int = 10) -> dict:
    """
    Build the JSON payload for the trademark search POST request.

    Investigation finding: the API accepts a structured search object with
    key='filing_number' to match the filing number field specifically.
    Using key='all' also works as a fallback (searches all fields).
    """
    return {
        "data": {
            "page": page,
            "perPage": per_page,
            "search": {
                "key": "filing_number",
                "value": filing_number,
            },
            "filter": {
                "province": [],
                "country": [],
                "status": [],
                "applicationType": [],
                "markFeature": [],
                "classification": [],
                "date": [],
                "fillDate": [],
                "regisDate": [],
                "receptionDate": [],
            },
            "advanceSearch": [
                {
                    "type": "all",
                    "strategy": "contains_word",
                    "selectedValues": [],
                    "inputValue": "",
                    "connectingOperator": "OR",
                }
            ],
            "isAdvanceSearch": False,
            "dateOption": "",
        }
    }


def build_detail_search_url(filing_number: str) -> str:
    """
    Build the SPA URL that triggers the search for a specific filing number.

    Investigation finding: The SPA reads URL query params on load and issues
    the corresponding POST to the search API. The 'all' key is recognized by
    the SPA's URL routing layer (filing_number key is only for direct API calls).
    """
    search_obj = json.dumps({"key": "all", "value": filing_number})
    filter_obj = json.dumps({
        "province": [], "country": [], "status": [], "applicationType": [],
        "markFeature": [], "classification": [], "date": [], "fillDate": [],
        "regisDate": [], "receptionDate": [],
    })
    advance_obj = json.dumps([{
        "type": "all", "strategy": "contains_word",
        "selectedValues": [], "inputValue": "", "connectingOperator": "OR",
    }])

    params = urllib.parse.urlencode({
        "tab": "0",
        "page": "1",
        "per_page": "20",
        "search": search_obj,
        "filter": filter_obj,
        "advance_search": advance_obj,
        "is_advanced_search": "false",
        "date_option": "",
    })
    return f"{SEARCH_URL}?{params}"


# ---------------------------------------------------------------------------
# Session Manager
# ---------------------------------------------------------------------------
class SessionManager:
    """
    Manages browser lifecycle and session cookie extraction.

    Browser use is limited to:
    1. One initial page load to obtain XSRF-TOKEN and laravel_session cookies.
    2. Per-trademark detail page rendering (save HTML).

    The browser instance is kept alive across all trademarks to reuse the session.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context: BrowserContext | None = None
        self.cookies: dict = {}

    async def start(self):
        log.info("[BROWSER] Launching headless Chromium...")
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(user_agent=USER_AGENT)

    async def initialize_cookies(self):
        """
        Single page load to obtain session cookies.
        The laravel backend sets XSRF-TOKEN and laravel_session on first visit.
        """
        log.info("[BROWSER] Initializing session cookies via page load...")
        page = await self._context.new_page()
        try:
            await page.goto(SEARCH_URL, timeout=PAGE_TIMEOUT_MS)
            # Wait for the SPA to initialize and the backend session to be set
            await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
        except Exception as e:
            log.warning(f"[BROWSER] Page load warning (cookies may still be valid): {e}")
        finally:
            await page.close()

        raw_cookies = await self._context.cookies()
        self.cookies = {c["name"]: c["value"] for c in raw_cookies}
        log.info(
            f"[BROWSER] Session initialized. Cookies: {[c for c in self.cookies if 'ga' not in c.lower()]}"
        )

    async def get_detail_page_html(self, filing_number: str, trademark_id: str) -> str:
        """
        Use the browser to navigate to the search page with the filing number,
        wait for results to render, attempt to click on the result row,
        and return the outer HTML of the page.

        This is the ONLY per-trademark browser operation.
        """
        log.info(f"[BROWSER] Getting detail HTML for {filing_number} (id={trademark_id})")
        page = await self._context.new_page()

        captured_search_response = {}

        async def handle_response(response):
            if "trademark-search" in response.url and response.status == 200:
                try:
                    body = await response.body()
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        captured_search_response["data"] = json.loads(body)
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            # Navigate to search URL that triggers the filing number search
            search_url = build_detail_search_url(filing_number)
            log.info(f"[BROWSER] Navigating to search URL for {filing_number}")
            await page.goto(search_url, timeout=PAGE_TIMEOUT_MS)

            # Wait for the API call and DOM rendering
            await page.wait_for_timeout(3000)
            try:
                await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
            except Exception:
                pass

            # Verify the search was executed
            if captured_search_response:
                items = captured_search_response["data"].get("data", {}).get("data", [])
                log.info(f"[BROWSER] Search returned {len(items)} results")

            # Try to click on the result row for the specific trademark
            clicked = False
            for selector in [
                f"tr[data-id='{trademark_id}']",
                "table tbody tr:first-child",
                "tbody tr:first-child",
                "[class*='result'] tr:first-child",
            ]:
                try:
                    el = await page.query_selector(selector)
                    if el and await el.is_visible():
                        text = await el.text_content() or ""
                        # Only click if this row contains the trademark we're looking for
                        fname_part = filing_number.replace("KH/", "").replace("/", "")
                        if trademark_id[3:] in text or fname_part in text or len(text) > 20:
                            await el.click()
                            await page.wait_for_timeout(2000)
                            try:
                                await page.wait_for_load_state("networkidle", timeout=5000)
                            except Exception:
                                pass
                            log.info(f"[BROWSER] Clicked result row. URL: {page.url}")
                            clicked = True
                            break
                except Exception as e:
                    log.debug(f"[BROWSER] Selector {selector} failed: {e}")

            if not clicked:
                log.info(f"[BROWSER] No clickable result found; saving search results page HTML")

            # Get full rendered HTML
            html = await page.content()
            log.info(f"[BROWSER] Captured HTML ({len(html):,} bytes)")
            return html

        finally:
            await page.close()

    async def stop(self):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        log.info("[BROWSER] Browser closed.")


# ---------------------------------------------------------------------------
# HTTP Client
# ---------------------------------------------------------------------------
class TrademarkHTTPClient:
    """
    Handles all pure HTTP operations:
    - Search API calls
    - Image downloads

    Uses session cookies obtained from the browser session.
    """

    def __init__(self, cookies: dict):
        xsrf_token = cookies.get("XSRF-TOKEN", "")
        self._client = httpx.AsyncClient(
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Referer": SEARCH_URL,
                "locale": "en",
                "X-XSRF-TOKEN": xsrf_token,
                "User-Agent": USER_AGENT,
            },
            cookies=cookies,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._client.aclose()

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        before=before_log(log, logging.DEBUG),
        after=after_log(log, logging.DEBUG),
    )
    async def search_trademark(self, filing_number: str) -> dict | None:
        """
        Search for a trademark by filing number via the REST API.
        Returns the first matching result dict, or None if not found.
        """
        log.info(f"[HTTP] Searching for filing number: {filing_number}")
        payload = build_search_payload(filing_number)

        response = await self._client.post(SEARCH_API, json=payload)
        response.raise_for_status()

        data = response.json()
        if not data.get("success"):
            log.warning(f"[HTTP] Search API returned success=false for {filing_number}: {data.get('message')}")
            return None

        items = data.get("data", {}).get("data", [])
        if not items:
            # Fallback: try with 'all' key
            log.info(f"[HTTP] No results with 'filing_number' key; trying 'all' key...")
            payload["data"]["search"] = {"key": "all", "value": filing_number}
            response = await self._client.post(SEARCH_API, json=payload)
            response.raise_for_status()
            data = response.json()
            items = data.get("data", {}).get("data", [])

        if not items:
            log.warning(f"[HTTP] No results found for {filing_number}")
            return None

        # Match the item by filing number in the 'number' field
        for item in items:
            number_field = item.get("number", "")
            filing_clean = filing_number.replace(" ", "")
            if filing_clean in number_field or filing_number in number_field:
                log.info(
                    f"[HTTP] Found match: id={item['id']!r} title={item.get('title', '').strip()!r}"
                )
                return item

        # If exact match not found, return first result
        log.warning(
            f"[HTTP] Exact match not found; using first result: {items[0].get('title', '').strip()}"
        )
        return items[0]

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        before=before_log(log, logging.DEBUG),
        after=after_log(log, logging.DEBUG),
    )
    async def download_image(self, trademark_id: str) -> bytes | None:
        """
        Download the trademark logo image.

        Investigation finding: The image endpoint requires /trademark-logo/{id}?type=ts_logo_thumbnail.
        The type=ts_logo variant returns a JSON response instead of image bytes.
        The laravel_session cookie is required for authenticated image access.
        """
        url = IMAGE_URL.format(trademark_id=trademark_id)
        log.info(f"[HTTP] Downloading image from: {url}")

        response = await self._client.get(url)

        if response.status_code == 404:
            log.warning(f"[HTTP] Image not found (404) for {trademark_id}")
            return None

        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if not any(t in content_type for t in ["image/", "jpeg", "jpg", "png", "gif"]):
            log.warning(f"[HTTP] Unexpected content type for image: {content_type}")
            if len(response.content) < 100:
                return None

        log.info(
            f"[HTTP] Image downloaded: {len(response.content):,} bytes, type={content_type}"
        )
        return response.content


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------
def save_html(content: str, filing_number: str) -> Path:
    filename = f"{filing_number_to_filename(filing_number)}_1.html"
    path = OUTPUT_DIR / filename
    path.write_text(content, encoding="utf-8")
    log.info(f"[OUTPUT] Saved HTML: {path} ({len(content):,} chars)")
    return path


def save_image(content: bytes, filing_number: str) -> Path:
    filename = f"{filing_number_to_filename(filing_number)}_2.jpg"
    path = OUTPUT_DIR / filename
    path.write_bytes(content)
    log.info(f"[OUTPUT] Saved image: {path} ({len(content):,} bytes)")
    return path


# ---------------------------------------------------------------------------
# Main scraper orchestration
# ---------------------------------------------------------------------------
class CambodiaIPScraper:
    """
    Orchestrates the scraping of trademark data from the Cambodia IP portal.

    Strategy:
    - Browser (Playwright, headless): Session initialization + HTML rendering
    - HTTP (httpx): Search API calls + image downloads

    The browser is launched ONCE and reused for all trademarks to maximize
    session reuse and minimize startup overhead.
    """

    def __init__(self, filing_numbers: list[str]):
        self.filing_numbers = filing_numbers
        self.session = SessionManager()
        self.results: dict[str, dict] = {}

    async def scrape_one(
        self,
        filing_number: str,
        http_client: TrademarkHTTPClient,
    ) -> bool:
        """
        Scrape a single trademark: get detail HTML + download image.
        Returns True on success, False on failure.
        """
        log.info(f"\n{'='*60}")
        log.info(f"Scraping: {filing_number}")
        log.info(f"{'='*60}")

        # --- Step 1: Search via HTTP API to get trademark data ---
        trademark_data = None
        try:
            trademark_data = await http_client.search_trademark(filing_number)
        except Exception as e:
            log.error(f"[SEARCH] Failed for {filing_number}: {e}")

        if trademark_data is None:
            log.error(f"[SEARCH] Trademark not found: {filing_number}. Skipping.")
            self.results[filing_number] = {"status": "not_found"}
            return False

        trademark_id = trademark_data.get("id", "")
        has_logo = trademark_data.get("logo", False)
        log.info(f"[SEARCH] Trademark ID: {trademark_id}, has_logo: {has_logo}")
        log.info(f"[SEARCH] Title: {trademark_data.get('title', '').strip()}")
        log.info(f"[SEARCH] Owner: {trademark_data.get('owner', '')}")
        log.info(f"[SEARCH] Status: {trademark_data.get('status', '')}")

        # --- Step 2: Get detail page HTML via browser ---
        html_path = None
        try:
            html_content = await self.session.get_detail_page_html(filing_number, trademark_id)
            html_path = save_html(html_content, filing_number)
        except Exception as e:
            log.error(f"[HTML] Failed to get detail page for {filing_number}: {e}")

        # --- Step 3: Download image via HTTP ---
        image_path = None
        if has_logo and trademark_id:
            try:
                image_bytes = await http_client.download_image(trademark_id)
                if image_bytes:
                    image_path = save_image(image_bytes, filing_number)
                else:
                    log.warning(f"[IMAGE] No image content returned for {filing_number}")
            except Exception as e:
                log.error(f"[IMAGE] Failed to download image for {filing_number}: {e}")
        else:
            log.info(f"[IMAGE] No logo available for {filing_number}, skipping image download")

        self.results[filing_number] = {
            "status": "success",
            "trademark_id": trademark_id,
            "data": trademark_data,
            "html_path": str(html_path) if html_path else None,
            "image_path": str(image_path) if image_path else None,
        }

        success = html_path is not None or image_path is not None
        if success:
            log.info(f"[DONE] {filing_number}: HTML={html_path}, Image={image_path}")
        else:
            log.warning(f"[DONE] {filing_number}: No files saved")

        return success

    async def run(self):
        """Main entry point: initialize session, scrape all trademarks."""
        OUTPUT_DIR.mkdir(exist_ok=True)

        # --- Phase 0: Initialize browser session (once) ---
        await self.session.start()
        try:
            await self.session.initialize_cookies()
        except Exception as e:
            log.error(f"Session initialization failed: {e}")
            await self.session.stop()
            raise

        # --- Phase 1: Scrape each trademark ---
        async with TrademarkHTTPClient(self.session.cookies) as http_client:
            for filing_number in self.filing_numbers:
                try:
                    await self.scrape_one(filing_number, http_client)
                except Exception as e:
                    log.error(f"Unexpected error scraping {filing_number}: {e}", exc_info=True)
                    self.results[filing_number] = {"status": "error", "error": str(e)}

        # --- Cleanup ---
        await self.session.stop()

        # --- Summary ---
        self._print_summary()

    def _print_summary(self):
        log.info(f"\n{'='*60}")
        log.info("SCRAPING SUMMARY")
        log.info(f"{'='*60}")
        success = 0
        for fn, result in self.results.items():
            status = result.get("status", "unknown")
            if status == "success":
                html = result.get("html_path", "N/A")
                img = result.get("image_path", "N/A")
                log.info(f"  [OK] {fn}: HTML={Path(html).name if html else 'N/A'}, Image={Path(img).name if img else 'N/A'}")
                success += 1
            elif status == "not_found":
                log.warning(f"  [NOT FOUND] {fn}")
            else:
                log.error(f"  [ERROR] {fn}: {result.get('error', status)}")
        log.info(f"{'='*60}")
        log.info(f"Total: {len(self.results)} | Success: {success} | Failed: {len(self.results) - success}")
        log.info(f"{'='*60}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main():
    log.info("Cambodia IP Portal - Trademark Scraper")
    log.info(f"Target filing numbers: {FILING_NUMBERS}")

    scraper = CambodiaIPScraper(FILING_NUMBERS)
    await scraper.run()


if __name__ == "__main__":
    asyncio.run(main())
