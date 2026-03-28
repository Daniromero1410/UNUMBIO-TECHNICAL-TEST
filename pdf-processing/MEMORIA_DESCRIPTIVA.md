# DISCLAIMER: Toda documentacion fue realizada en ingles por motivos de universalidad.

# PDF Bulletin Processor — Descriptive Report

Script: `pdf_processor.py`
Input:  `BUL_EM_TM_2024000007_001.json`
Output: `BUL_EM_TM_2024000007_002.json`

---

## 1. Input Analysis

Before writing any code, I opened both the reference PDF (`BUL_EM_TM_2024000001_000.pdf`) and its extracted JSON (`BUL_EM_TM_2024000001_001.json`) side by side to understand the coordinate system.

**JSON structure per page:**
```json
{
  "page": 203,
  "textboxhorizontal": [
    { "text": "111", "x0": 57.6, "x1": 72.4, "top": 97.4, "bottom": 109.4 },
    { "text": "018255615", "x0": 86.0, "x1": 159.5, "top": 97.4, "bottom": 109.4 },
    ...
  ]
}
```

Key observations from coordinate inspection:

- The page is approximately 595 units wide (A4 landscape equivalent).
- **Left column** data: x0 in range [57, ~235].
- **Right column** data: x0 in range [312, ~540].
- **Centre gutter** (x0 ~250-305): used for section headings like "B.1.", "B.2.", "PART B".
- **INID label strip (left column)**: x0 approximately 50-80 (narrow strip, always 3-digit codes).
- **INID label strip (right column)**: x0 approximately 305-330.
- Headers and footers: `top < 60` or `top > 800`.

---

## 2. Challenges and Solutions

### Challenge 1 — Section Filtering

**Problem:** The input file contains the entire bulletin (166 pages). Only Section B.1 records must be extracted.

**Solution:** I searched for centre-gutter elements (250 < x0 < 320) matching `B.1.` to mark the section start, and `B.2.` (or any `B.[2-9]`) to mark the section end. This yields the array index range `[87, 120]` (pages 89-122).

```python
def find_b1_bounds(pages):
    for i, page in enumerate(pages):
        for tb in page["textboxhorizontal"]:
            if 250.0 < tb["x0"] < 320.0:
                if re.match(r"^B\.1\.?$", tb["text"].strip()):
                    start_idx = i
                elif re.match(r"^B\.[2-9]\.?$", tb["text"].strip()):
                    return start_idx, i
```

---

### Challenge 2 — Column Reconstruction

**Problem:** PDFPlumber emits all text boxes on a page as a flat list, with no column structure. Naively sorting by `top` would interleave left and right column data.

**Problem example:** A left-column line at `top=150` and a right-column line at `top=151` would be merged into a single logical line, corrupting the record stream.

**Solution:** Split elements by a single `COLUMN_SPLIT_X = 250.0` threshold before any sorting or grouping. Left and right columns are processed as independent ordered sequences, always left first (newspaper reading order).

```python
left_col  = [tb for tb in data_els if tb["x0"] < COLUMN_SPLIT_X]
right_col = [tb for tb in data_els if tb["x0"] >= COLUMN_SPLIT_X]

for col in (left_col, right_col):
    ...  # process each column independently
```

---

### Challenge 3 — INID Code Identification

**Problem:** INID codes (111, 151, 210, 400, 450) are 3-digit numbers — but so are page numbers, registration-number fragments, and other values that appear on the page.

**Solution:** Combine two checks: (1) the text matches `^\d{3}$`, and (2) the x0 coordinate falls within a known INID gutter strip (left: 50-80, right: 305-330). Any 3-digit number outside those strips is treated as data, not a label.

```python
def is_inid_code(text, x0):
    if not re.match(r"^\d{3}$", text):
        return False
    return (50.0 <= x0 <= 80.0) or (305.0 <= x0 <= 330.0)
```

---

### Challenge 4 — Line Grouping

**Problem:** PDFPlumber reports `top` values with sub-pixel precision. Two text boxes that are visually on the same line may have `top` values differing by 0.5-1.0 points.

**Solution:** Group elements within a `LINE_TOP_TOLERANCE = 1.5` threshold. The first element's `top` becomes the group representative key, and subsequent elements within tolerance are merged into it.

---

### Challenge 5 — Cross-Column and Cross-Page Record Splits

**Problem:** A single trademark record can be split across two columns (its first INID fields appear at the bottom of the left column; continuation fields appear at the top of the right column on the same page). Records can also span page breaks.

**Solution:** There is no special handling needed. Because the reading order is maintained as a linear stream — left column top-to-bottom, then right column top-to-bottom, page by page — a record that starts in the left column simply continues accumulating fields when the right column is processed next. The `current` record dict persists across column and page boundaries until a new `111` code is encountered.

This design means cross-column and cross-page splits are transparent: they require zero additional logic beyond maintaining the correct reading order.

---

### Challenge 6 — Field Type Handling

**Problem:** INID 400 (prior filing history) can appear multiple times within a single record, once per prior application. All other fields appear exactly once.

**Solution:** Field 400 is stored as a list (`LIST_FIELDS = {"400"}`), using `.setdefault(inid, []).append(value_text)`. All other fields are stored as plain strings, matching the reference output schema exactly.

---

## 3. Approach Summary

| Phase | Description |
|-------|-------------|
| Coordinate analysis | Measured x0 ranges for columns, INID gutters, headers, footers from both bulletins |
| Section detection | Regex on centre-gutter text to find B.1 start/end page indices |
| Column separation | Hard split at x0=250; process left before right (newspaper order) |
| Line grouping | Sub-pixel tolerance grouping on `top` coordinate |
| Record assembly | State machine: `111` flushes previous record and starts a new one |
| Field typing | INID 400 as list; all others as str; `_PAGE` as int |

---

## 4. Output Validation

```
Total records extracted : 551
Pages covered           : 89-122
Missing fields          : 0 (all records have 111, 151, 450, 210, 400)
Type errors             : 0 (_PAGE is int; 400 is list of str)
Records per page        : 11-17 (consistent with two-column A4 layout)
```

First record:
```json
{
  "_PAGE": 89,
  "111": "018386578",
  "151": "10/01/2024",
  "450": "11/01/2024",
  "210": "018386578",
  "400": ["03/10/2023 - 2023/187 - A.1"]
}
```

---

## 5. Design Decisions

- **Standard library only:** The script uses only `json` and `re` — no third-party dependencies.
- **No hardcoded page numbers:** Section boundaries are detected dynamically from text content, so the same script works on any bulletin that follows the same format.
- **Constants at the top:** All geometric thresholds (`COLUMN_SPLIT_X`, gutter ranges, header/footer limits) are named constants with explanatory comments, making calibration for different bulletins straightforward.

---

## 6. Time Invested

| Phase | Description | Time |
|-------|-------------|------|
| JSON structure analysis | Coordinate mapping, column geometry, INID gutter zones | ~45 min |
| Script implementation | Section detection, column split, record assembly, typing | ~1 h |
| Validation | Output comparison against reference schema, field type checks | ~15 min |
| Documentation | This report | ~30 min |
| **Total** | | **~2.5 hours** |
