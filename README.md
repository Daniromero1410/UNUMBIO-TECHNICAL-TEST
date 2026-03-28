# DISCLAIMER: Toda documentacion fue realizada en ingles por motivos de universalidad.

# UNUMBIO Technical Tests

This repository contains the deliverables for two technical tests assigned by UNUMBIO SpA.

---

## Repository Structure

```
UNUMBIO-TECHNICAL-TEST/
├── web-scraping/
│   ├── scraper.py          # Async scraper (Playwright + httpx)
│   ├── requirements.txt    # Python dependencies
│   └── output/             # Downloaded HTML pages and trademark images
│       ├── KH4963312_1.html
│       ├── KH4963312_2.jpg
│       ├── KH5928614_1.html
│       ├── KH5928614_2.jpg
│       ├── KH8349819_1.html
│       └── KH8349819_2.jpg
└── pdf-processing/
    ├── pdf_processor.py        # Section B.1 extractor (standard library only)
    ├── MEMORIA_DESCRIPTIVA.md  # Descriptive report for this test
    └── output/
        └── BUL_EM_TM_2024000007_002.json  # 551 extracted trademark records
```

---

## Test 1 — Web Scraping

### Objective

Build an async Python scraper that downloads trademark detail pages and images from the Cambodia Intellectual Property portal for three specific filing numbers:

- `KH/49633/12`
- `KH/59286/14`
- `KH/83498/19`

**Target:** https://digitalip.cambodiaip.gov.kh/en/trademark-search

### Technical Stack

| Component | Technology | Reason |
|-----------|-----------|--------|
| Browser automation | Playwright (async) | Session initialization and SPA rendering |
| HTTP client | httpx (async) | Direct API calls and image downloads without browser overhead |
| Retry logic | tenacity | Exponential backoff on transient network failures |
| Language | Python 3.10+ | Required by the specification |

### Installation

```bash
cd web-scraping

# Install Python dependencies
pip install -r requirements.txt

# Install the Chromium browser (used by Playwright)
python -m playwright install chromium
```

### Running the Scraper

```bash
python scraper.py
```

Files are saved to `web-scraping/output/`:

| File | Content |
|------|---------|
| `KH4963312_1.html` | Rendered detail page for KH/49633/12 (MINGFAI) |
| `KH4963312_2.jpg` | Trademark image for KH/49633/12 |
| `KH5928614_1.html` | Rendered detail page for KH/59286/14 (Eesatto) |
| `KH5928614_2.jpg` | Trademark image for KH/59286/14 |
| `KH8349819_1.html` | Rendered detail page for KH/83498/19 (FORCE) |
| `KH8349819_2.jpg` | Trademark image for KH/83498/19 |

### Thought Process

#### 1. Initial Assessment

The first step was identifying what kind of site we are dealing with. A simple `requests.get()` on the search URL returned only the SPA shell — no trademark data. The `<head>` tags confirmed **Nuxt.js / Vue.js**, meaning all content is rendered client-side by JavaScript. Standard HTML parsing was ruled out immediately.

Key observations:
- No CAPTCHA present.
- No login required for search.
- Backend framework: **Laravel** (identified from cookie names).

#### 2. Network Interception

The correct approach for an SPA is to run a real browser with network interception to capture actual API calls. I automated this with Playwright's request/response listeners.

Endpoints captured on page load:

```
GET  /api/v1/web/get-setting
POST /api/v1/web/trademark-search
GET  /trademark-logo/{id}?type=ts_logo_thumbnail
```

Session cookies set by Laravel:
- `XSRF-TOKEN` — must be forwarded as `X-XSRF-TOKEN` request header on all POST requests (419 without it).
- `laravel_session` — required for image downloads (returns JSON error without it).

#### 3. API Key Discovery

The auto-fired search used `"key": "all"`. I tested 10 candidate keys to find which one targets the filing number field:

```
filing_number    -> correct result (MINGFAI for KH/49633/12)
filingNumber     -> 0 results
all              -> matches all fields
registrationNumber, applicationNumber, number, ... -> 0 results
```

Winner: `"key": "filing_number"`.

#### 4. Image Endpoint

Two variants exist:

```
GET /trademark-logo/{id}?type=ts_logo          -> returns application/json (not an image)
GET /trademark-logo/{id}?type=ts_logo_thumbnail -> returns image/jpeg
```

The `_thumbnail` variant is the one that returns binary image data. Without the `laravel_session` cookie the endpoint returns a JSON error regardless.

#### 5. Detail Page

There is no dedicated detail page URL. The SPA renders trademark details within the search results view based on URL query parameters. I navigated Playwright to the search URL with the filing number as the query string and waited for the SPA to render. That rendered HTML is saved as the detail page.

#### 6. Architecture Decision

```
Operations that require the browser:
  - Session initialization (cookies are set on first page visit)
  - Per-trademark detail page HTML (JavaScript rendering required)

Operations that do not require the browser:
  - Trademark search (direct POST to REST API with session cookies)
  - Image download (direct GET with session cookie)
```

Final architecture: one shared Playwright instance launched once for session extraction, reused for per-trademark HTML rendering. All HTTP calls (search, image) go through a shared async httpx client. This minimizes browser usage and keeps the scraper fast.

#### 7. Error Handling

| Scenario | Behavior |
|----------|---------|
| Trademark not found | Log warning and continue |
| Network timeout | tenacity retries up to 3 times with exponential backoff |
| Image unavailable | Skip download and log |
| Image endpoint returns JSON | Detected by content-type check, treated as unavailable |
| CSRF mismatch (419) | `raise_for_status()` triggers tenacity retry |

### Output Evidence

| Filing Number | Trademark | Owner | Status |
|---------------|-----------|-------|--------|
| KH/49633/12 | MINGFAI | Ming Fai Enterprise International Co., Ltd. | Inactive |
| KH/59286/14 | Eesatto | DIAMOND POINT Sdn Bhd | Inactive |
| KH/83498/19 | FORCE | TIFORCE INTERNATIONAL CO., LTD. | Active |

### Time Invested

| Phase | Description | Time |
|-------|-------------|------|
| Site investigation | Network interception, API key discovery, cookie analysis | ~1.5 h |
| Implementation | Architecture, async code, error handling, debugging | ~1 h |
| Documentation | README, code comments, repository setup | ~30 min |
| **Total** | | **~3 hours** |

---

## Test 2 — PDF Processing

### Objective

Write a Python script that processes `BUL_EM_TM_2024000007_001.json` (coordinates extracted from a trademark bulletin by PDFPlumber) to extract exclusively the Section B.1 trademark records and produce `BUL_EM_TM_2024000007_002.json`.

### Technical Stack

| Component | Technology | Reason |
|-----------|-----------|--------|
| JSON parsing | `json` (stdlib) | Standard library, no external dependencies needed |
| Pattern matching | `re` (stdlib) | Section heading detection via regex |
| Language | Python 3.10+ | Required by the specification |

No third-party libraries are required. The input JSON already contains all coordinate data.

### Running the Script

```bash
cd pdf-processing
python pdf_processor.py
```

Output is written to `pdf-processing/output/BUL_EM_TM_2024000007_002.json`.

### Output Format

```json
{
  "B": {
    "1": [
      {
        "_PAGE": 89,
        "111": "018386578",
        "151": "10/01/2024",
        "450": "11/01/2024",
        "210": "018386578",
        "400": ["03/10/2023 - 2023/187 - A.1"]
      },
      ...
    ]
  }
}
```

Field types:
- `_PAGE`: `int` — page number where the record starts.
- `400`: `list[str]` — one entry per prior filing line.
- All other INID fields: `str`.

### Thought Process

#### 1. Input Analysis

Before writing code I inspected both the reference bulletin (`BUL_EM_TM_2024000001`) and the target bulletin side by side (PDF and JSON) to map the coordinate system.

Key measurements:

| Zone | x0 range |
|------|---------|
| Left column INID gutter | 50 - 80 |
| Left column data | 86 - 235 |
| Centre gutter (section headings) | 250 - 305 |
| Right column INID gutter | 305 - 330 |
| Right column data | 336 - 540 |
| Far-right edge (headers/footers) | > 540 |

Vertical exclusion zones: `top < 60` (page header) and `top > 800` (page footer).

#### 2. Section Filtering

The input file contains 166 pages covering the entire bulletin. Section B.1 is identified dynamically by scanning for centre-gutter elements matching the regex `^B\.1\.?$` (start) and `^B\.[2-9]\.?$` (end). This yields pages 89-122 without hardcoding any page numbers.

#### 3. Column Reconstruction

PDFPlumber emits all text boxes as a flat list per page. Naively sorting by `top` would interleave left and right columns. The solution is a hard split at `x0 = 250`: elements below the threshold go to the left column list, the rest to the right column list. Each list is then sorted and processed independently.

Reading order: left column top-to-bottom, then right column top-to-bottom (standard newspaper layout).

#### 4. INID Code Detection

INID codes (111, 151, 210, 400, 450) match `^\d{3}$` — but so do page numbers and data fragments. To disambiguate, a coordinate filter is applied: a 3-digit token is an INID code only when its x0 falls in a known gutter strip (left: 50-80, right: 305-330). Any 3-digit number outside those strips is treated as data.

#### 5. Cross-Column and Cross-Page Splits

Records can begin at the bottom of the left column and continue at the top of the right column. They can also span page boundaries. No special handling is required: because the reading order is maintained as a single linear stream across columns and pages, the `current` record dict simply continues accumulating fields. A record is only flushed when the next INID 111 is encountered.

#### 6. Field Type Handling

INID 400 (prior filing history) appears once per prior application and is stored as a list. All other INID fields appear exactly once per record and are stored as strings. This matches the reference schema exactly.

### Output Validation

```
Total records : 551
Pages covered : 89-122
Missing fields : 0
Type errors    : 0
Records per page : 11-17 (consistent with two-column A4 layout)
```

### Time Invested

| Phase | Description | Time |
|-------|-------------|------|
| JSON structure analysis | Coordinate mapping, column geometry, INID gutter zones | ~45 min |
| Implementation | Section detection, column split, record assembly | ~1 h |
| Validation | Output comparison against reference schema, type checks | ~15 min |
| Documentation | Descriptive report and README | ~30 min |
| **Total** | | **~2.5 hours** |
