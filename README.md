# DISCLAIMER: Toda documentacion fue realizada en ingles por motivos de universalidad.

# Cambodia IP — Trademark Scraper

Async Python scraper that downloads trademark detail pages and images from the Cambodia Intellectual Property portal.

**Target:** [https://digitalip.cambodiaip.gov.kh/en/trademark-search](https://digitalip.cambodiaip.gov.kh/en/trademark-search)

---

## Requirements

- Python 3.10+
- pip

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/Daniromero1410/UNUMBIO-TECHNICAL-TEST.git
cd UNUMBIO-TECHNICAL-TEST

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install the Chromium browser (used by Playwright)
python -m playwright install chromium
```

---

## Running the scraper

```bash
python scraper.py
```

Files are saved to `output/`:

```
output/
  KH4963312_1.html    # Detail page — KH/49633/12  (MINGFAI)
  KH4963312_2.jpg     # Trademark image — KH/49633/12
  KH5928614_1.html    # Detail page — KH/59286/14  (Eesatto)
  KH5928614_2.jpg     # Trademark image — KH/59286/14
  KH8349819_1.html    # Detail page — KH/83498/19  (FORCE)
  KH8349819_2.jpg     # Trademark image — KH/83498/19
```

---

## Technical Stack

| Component          | Technology                  | Reason                                               |
|--------------------|-----------------------------|------------------------------------------------------|
| Browser automation | Playwright (async)          | Session initialization + SPA rendering              |
| HTTP client        | httpx (async)               | Direct API calls + image downloads (no browser)     |
| Retry logic        | tenacity                    | Exponential backoff on transient network failures   |
| Language           | Python 3.10+                | Required by the technical specification             |

---

## Thought Process

### 1. Initial Assessment — What kind of site is this?

The first step was understanding what we're dealing with before writing a single line of scraping code.

A quick inspection of the page source (`requests.get()`) revealed only the SPA shell — no trademark data. The `<head>` showed Nuxt.js meta tags, confirming this is a **Vue.js/Nuxt.js Single Page Application** rendered entirely client-side. That ruled out simple HTML parsing.

**Key observations:**
- The page title and `data-n-head` attributes confirmed Nuxt.js
- No CAPTCHA present
- No login required for search
- Backend framework: **Laravel** (identified later from cookie names)

### 2. Network Interception — Finding the Real API

The correct approach for any SPA is to open a non-headless browser, watch the DevTools Network tab, and capture what actually happens. I automated this with Playwright's request/response interceptors.

**What I captured on page load:**

```
GET  /api/v1/web/get-setting         → App configuration (feature flags, icons)
POST /api/v1/web/trademark-search    → Main search endpoint (fires automatically on load)
GET  /trademark-logo/{id}?type=ts_logo_thumbnail  → Logo images
```

**The search POST body (auto-fired by the SPA on load):**
```json
{
  "data": {
    "page": 1,
    "perPage": 20,
    "search": { "key": "all", "value": "" },
    "filter": { "province": [], "country": [], "status": [], ... },
    "advanceSearch": [{ "type": "all", "strategy": "contains_word", ... }],
    "isAdvanceSearch": false,
    "dateOption": ""
  }
}
```

**Session cookies set by Laravel:**
- `XSRF-TOKEN` — CSRF protection, must be sent as `X-XSRF-TOKEN` request header
- `laravel_session` — Session identifier, required for image access

**Critical discovery:** The `X-XSRF-TOKEN` header is **required** for all POST requests. Without it the server returns 419 (CSRF token mismatch). Without `laravel_session`, image requests return a JSON error instead of the image binary.

### 3. API Key Discovery — How to search by filing number

The initial auto-search used `"key": "all"`. I needed to find which key value targets the filing number field specifically.

I tested 10 candidate keys via direct `httpx` calls (no browser needed at this point):

```
filingNumber        → 0 results
filing_number       → ✅ returns MINGFAI for KH/49633/12
applicationNumber   → 0 results
application_number  → 0 results
registrationNumber  → 0 results
all                 → ✅ returns MINGFAI (searches all fields)
number              → 0 results
filing              → 0 results
```

**Winner: `"key": "filing_number"`** — returns an exact match for the filing number field.

**Search response structure (key fields):**
```json
{
  "id": "KHT201249633",
  "title": "MINGFAI",
  "logo": true,
  "owner": "Ming Fai Enterprise International Co., Ltd.",
  "number": "KH/49633/12 (31-12-2012)",
  "status": "Inactive (30-06-2023)",
  "application_number": "KHT201249633",
  "registration_number": "N/A",
  "type_of_mark": "Combined",
  "application_date": "31-12-2012",
  "nice_class": "35"
}
```

**Trademark ID format:** `KHT` + `{4-digit year}` + `{serial number}`
- `KH/49633/12` → `KHT201249633`
- `KH/59286/14` → `KHT201459286`
- `KH/83498/19` → `KHT201983498`

### 4. Image Endpoint — The Cookie Requirement

Confirmed via testing with and without cookies:

```
GET /trademark-logo/KHT201249633?type=ts_logo_thumbnail
  → WITH laravel_session:    200 image/jpeg (3,746 bytes) ✅
  → WITHOUT laravel_session: 200 application/json (error payload) ✗
```

Also discovered: `?type=ts_logo` (without `_thumbnail`) returns JSON, not an image. The `ts_logo_thumbnail` variant is the one that returns binary image data.

### 5. Detail Page — How the SPA presents detail information

There is **no dedicated detail page URL**. The SPA presents trademark detail within the search results page. When you navigate with search parameters in the URL, the SPA reads them, issues the corresponding POST to the search API, and renders the results inline.

I tested several potential detail URL patterns (e.g., `/en/trademark/{id}`, `/api/v1/web/trademark/{id}`) — all returned 404 or rendered only the generic SPA shell without trademark-specific data.

**Approach:** Navigate Playwright to the search URL with the filing number as the query parameter. The SPA fires the search API automatically and renders the result. That rendered HTML is the detail page.

### 6. Architecture Decision — What needs a browser vs. pure HTTP

This is the core of the performance question. Every browser operation costs ~10-50x more than an HTTP request in memory and CPU.

```
Operations that NEED the browser:
  ✓ Session initialization (laravel_session + XSRF-TOKEN are set on first page visit)
  ✓ Detail page HTML (SPA renders from JavaScript — static GET returns a shell)

Operations that do NOT need the browser:
  ✓ Trademark search (pure POST to REST API with session cookies)
  ✓ Image download (pure GET with laravel_session cookie)
```

**Final architecture:**

```
Playwright (headless, shared instance — launched once):
  ├── [Once]          Load search page → extract session cookies
  └── [Per trademark] Navigate to search URL → wait for render → save HTML

httpx (async, shared client — all HTTP-only operations):
  ├── [Per trademark] POST /api/v1/web/trademark-search
  └── [Per trademark] GET  /trademark-logo/{id}?type=ts_logo_thumbnail
```

By sharing one browser context across all trademarks, we pay the browser startup cost only once. Each per-trademark browser operation is limited to a single page navigation.

### 7. Error Handling Strategy

| Scenario | Behavior |
|----------|----------|
| Trademark not found | Log warning, skip to next (do not crash) |
| Network timeout | tenacity retries up to 3 times with exponential backoff |
| Image unavailable (`logo: false`) | Skip image download, log and continue |
| Image endpoint returns JSON | Detected by content-type check, treated as unavailable |
| CSRF mismatch (419) | httpx.raise_for_status() triggers tenacity retry |

---

## Output Evidence

All 6 output files are included in this repository as proof of successful execution:

| # | Filing Number | Trademark | Owner | Status |
|---|---------------|-----------|-------|--------|
| 1 | KH/49633/12 | MINGFAI | Ming Fai Enterprise International Co., Ltd. | Inactive |
| 2 | KH/59286/14 | Eesatto | DIAMOND POINT Sdn Bhd | Inactive |
| 3 | KH/83498/19 | FORCE | TIFORCE INTERNATIONAL CO., LTD. | Active |

---

## Time Invested

| Phase | Description | Time |
|-------|-------------|------|
| Site investigation | Network interception, API key discovery, cookie analysis | ~1.5 h |
| Scraper implementation | Architecture, async code, error handling, debugging | ~1 h |
| Documentation & README | README, code comments, repository setup | ~30 min |
| **Total** | | **~3 hours** |
