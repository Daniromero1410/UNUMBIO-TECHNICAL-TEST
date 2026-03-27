# DISCLAIMER: Toda documentacion fue realizada en ingles por motivos de universalidad.

# Cambodia IP - Trademark Scraper

An async Python web scraper that downloads trademark data from the Cambodia Intellectual Property portal ([digitalip.cambodiaip.gov.kh](https://digitalip.cambodiaip.gov.kh/en/trademark-search)).

## Stack

| Component         | Technology                    |
|-------------------|-------------------------------|
| Browser automation| Playwright (async, headless)  |
| HTTP requests     | httpx (async)                 |
| Retry logic       | tenacity                      |
| Language          | Python 3.10+                  |

---

## Installation

**1. Clone the repository**

```bash
git clone <repo-url>
cd cambodia-trademark-scraper
```

**2. Install Python dependencies**

```bash
pip install -r requirements.txt
```

**3. Install Playwright browser (Chromium only)**

```bash
python -m playwright install chromium
```

---

## Usage

```bash
python scraper.py
```

Output files are written to the `output/` directory:

```
output/
  KH4963312_1.html    # Detail page for KH/49633/12
  KH4963312_2.jpg     # Trademark image for KH/49633/12
  KH5928614_1.html    # Detail page for KH/59286/14
  KH5928614_2.jpg     # Trademark image for KH/59286/14
  KH8349819_1.html    # Detail page for KH/83498/19
  KH8349819_2.jpg     # Trademark image for KH/83498/19
```

---

## Technical Design

### Thought Process and Site Investigation

The site is a **Nuxt.js SPA** (Single Page Application) backed by a **Laravel REST API**. The approach to understand it:

#### Phase 0 — Initial HTTP Recon
First attempt: pure `requests.get()` on the search page. Returns the SPA shell HTML — no trademark data. Confirmed: JavaScript rendering required.

#### Phase 1 — Network Interception with Playwright
Launched a non-headless browser and captured all non-asset HTTP calls while the page loaded. Findings:

| Call | Method | URL | Purpose |
|------|--------|-----|---------|
| Settings | GET | `/api/v1/web/get-setting` | App configuration |
| Search | POST | `/api/v1/web/trademark-search` | Main search endpoint |
| Images | GET | `/trademark-logo/{id}?type=ts_logo_thumbnail` | Logo images |

The search API fires automatically on page load with `key: "all", value: ""`.

**Cookies set by the backend:**
- `XSRF-TOKEN` — CSRF protection token (sent as `X-XSRF-TOKEN` header)
- `laravel_session` — Session identifier required for image access

#### Phase 2 — API Key Discovery via httpx
Tested 10 candidate search key values via direct HTTP calls to find the correct field for filing number search. Confirmed: `key: "filing_number"` returns exact matches.

**Search payload structure (discovered):**
```json
{
  "data": {
    "page": 1,
    "perPage": 10,
    "search": { "key": "filing_number", "value": "KH/49633/12" },
    "filter": { "province": [], "country": [], ... },
    "advanceSearch": [{ "type": "all", "strategy": "contains_word", ... }],
    "isAdvanceSearch": false,
    "dateOption": ""
  }
}
```

**Response structure (key fields):**
```json
{
  "id": "KHT201249633",
  "title": "MINGFAI",
  "logo": true,
  "owner": "Ming Fai Enterprise International Co., Ltd.",
  "number": "KH/49633/12 (31-12-2012)",
  "status": "Inactive (30-06-2023)",
  "application_number": "KHT201249633"
}
```

**Trademark ID format:** `KHT` + `{4-digit year}` + `{serial number}`. E.g., `KH/49633/12` → `KHT201249633`.

#### Phase 3 — Image Endpoint Investigation
Tested `/trademark-logo/{id}?type=ts_logo_thumbnail`:
- Returns `image/jpeg` with session cookies present
- Returns JSON error without cookies
- Confirmed: laravel_session cookie is the gating factor

#### Phase 4 — Detail Page Analysis
The SPA has no separate detail page URL. The trademark detail is rendered in-place after a search. The browser navigates using URL query parameters, and the SPA issues the corresponding POST to the search API. The rendered HTML with search results is saved as the detail page.

### Architecture Decision: Hybrid Browser + HTTP

The key design principle: **use the browser only when unavoidable**.

```
Browser (Playwright, headless, shared):
  ├── [Once]       Initialize session → extract XSRF-TOKEN + laravel_session
  └── [Per mark]   Navigate to search URL → render SPA → save HTML

HTTP (httpx, async):
  ├── [Per mark]   POST /api/v1/web/trademark-search → get trademark data
  └── [Per mark]   GET /trademark-logo/{id}?type=ts_logo_thumbnail → download image
```

**Why not use the browser for everything?** Browser context is 10-50x more expensive than an HTTP request. Once we have the session cookies, API calls and image downloads are plain HTTP — no JavaScript needed.

**Why can't we skip the browser entirely?** The Laravel backend requires a valid `laravel_session` cookie (set on first page visit). Without it, the image endpoint returns an error JSON. The session is established through the normal browser page load flow.

### Error Handling

| Scenario | Behavior |
|----------|----------|
| Trademark not found | Log warning, skip to next |
| Network timeout | Retry up to 3 times with exponential backoff (tenacity) |
| No image available | Log warning, continue without image |
| API rate limit (60 req/min) | tenacity retry handles transient failures |

---

## Time Invested

| Phase | Task | Time |
|-------|------|------|
| 1 | Site investigation (network interception, API key discovery) | ~1.5 hours |
| 2 | Scraper implementation and debugging | ~1 hour |
| 3 | Documentation and README | ~30 minutes |
| **Total** | | **~3 hours** |

---

## Output Evidence

All 6 files are included in the `output/` directory as evidence of successful execution:

| Filing Number | Trademark | Owner | HTML | Image |
|---------------|-----------|-------|------|-------|
| KH/49633/12 | MINGFAI | Ming Fai Enterprise International Co., Ltd. | KH4963312_1.html | KH4963312_2.jpg |
| KH/59286/14 | Eesatto | DIAMOND POINT Sdn Bhd | KH5928614_1.html | KH5928614_2.jpg |
| KH/83498/19 | FORCE | TIFORCE INTERNATIONAL CO., LTD. | KH8349819_1.html | KH8349819_2.jpg |
