# DISCLAIMER: Toda documentacion fue realizada en ingles por motivos de universalidad.

"""
PDF Bulletin B.1 Section Extractor
====================================
Processes BUL_EM_TM_2024000007_001.json to extract Section B.1 trademark
records and outputs BUL_EM_TM_2024000007_002.json.

Challenges solved:
  1. Section filtering  - Identify B.1 start/end from text hierarchy.
  2. Column reconstruction - Use x0/x1 to separate left and right columns.
  3. Record identification - INID code 111 signals the start of each record.
  4. Multi-column/page splits - Reading order is left-col then right-col per page;
     a record that starts at the bottom of the left column continues at the top
     of the right column (newspaper layout).
"""

import json
import re

# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

INPUT_FILE = "BUL_EM_TM_2024000007_001.json"
OUTPUT_FILE = "BUL_EM_TM_2024000007_002.json"

# ---------------------------------------------------------------------------
# Layout constants  (derived from coordinate analysis of the bulletin)
# ---------------------------------------------------------------------------

# x0 threshold that separates the left data column from the right data column.
# Left column:  x0  in [ ~57,  ~180 ]
# Right column: x0  in [ ~312, ~540 ]
COLUMN_SPLIT_X: float = 250.0

# INID code gutter zones: narrow strips between the margin and the data.
# Left gutter:  x0  in [ 50,  80  ]
# Right gutter: x0  in [ 305, 330 ]
LEFT_INID_X = (50.0, 80.0)
RIGHT_INID_X = (305.0, 330.0)

# Vertical zones to exclude (page headers and footers).
# Headers (section labels, EUTM continuation markers) sit at top < HEADER_BOTTOM.
# Footers (bulletin number, page number) sit at top > FOOTER_TOP.
HEADER_BOTTOM: float = 60.0
FOOTER_TOP: float = 800.0

# Elements in the centre gutter (section headings) and far right (footers/headers)
# are not data; they are filtered before column assignment.
CENTRE_GUTTER_X = (250.0, 305.0)
FAR_RIGHT_X_MIN: float = 540.0

# Vertical tolerance for grouping text boxes that belong to the same line.
LINE_TOP_TOLERANCE: float = 1.5

# Pattern for INID codes: exactly 3 decimal digits.
_INID_RE = re.compile(r"^\d{3}$")

# Field 400 is stored as a list of strings (one entry per line/occurrence).
LIST_FIELDS = {"400"}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def clean(text: str) -> str:
    """Collapse all whitespace (including newlines) to single spaces."""
    return " ".join(text.split())


def is_inid_code(text: str, x0: float) -> bool:
    """
    Return True when *text* is a 3-digit INID label positioned in a gutter zone.

    INID codes (e.g. 111, 151, 210, 400, 450) always appear in narrow gutter
    strips to the left of each data column.  Any 3-digit number outside those
    strips (e.g. a page number that happens to be 3 digits) is not an INID.
    """
    if not _INID_RE.match(text):
        return False
    l_min, l_max = LEFT_INID_X
    r_min, r_max = RIGHT_INID_X
    return (l_min <= x0 <= l_max) or (r_min <= x0 <= r_max)


def is_data_element(tb: dict) -> bool:
    """
    Return True for elements that belong to the B.1 data area.

    Excluded:
    - Page headers / footer text (top outside the vertical data band).
    - Centre-gutter text (section headings like 'PART B', 'B.1.').
    - Far-right text (header/footer markers like 'Part B.1.' on the right edge).
    """
    top = tb["top"]
    x0 = tb["x0"]
    if top < HEADER_BOTTOM or top > FOOTER_TOP:
        return False
    if CENTRE_GUTTER_X[0] <= x0 <= CENTRE_GUTTER_X[1]:
        return False
    if x0 >= FAR_RIGHT_X_MIN:
        return False
    return True


def group_by_line(elements: list) -> dict:
    """
    Group elements by their vertical position.

    Two elements are considered on the same line when their *top* values
    differ by at most LINE_TOP_TOLERANCE pixels.  This accounts for minor
    sub-pixel rendering variations within PDFPlumber output.

    Returns an ordered dict: representative_top → [elements].
    """
    groups: dict[float, list] = {}
    for tb in sorted(elements, key=lambda e: e["top"]):
        top = tb["top"]
        matched = False
        for key in groups:
            if abs(top - key) <= LINE_TOP_TOLERANCE:
                groups[key].append(tb)
                matched = True
                break
        if not matched:
            groups[top] = [tb]
    return groups


# ---------------------------------------------------------------------------
# Section boundary detection
# ---------------------------------------------------------------------------

def find_b1_bounds(pages: list) -> tuple[int, int]:
    """
    Return (start_idx, end_idx) — array indices into *pages* — for B.1.

    Detection strategy:
    - B.1 START: page containing a centred 'B.1.' text element (the section
      heading, placed in the centre gutter at x0 ≈ 283).
    - B.1 END: page containing the next section heading at the same position
      (e.g. 'B.2.').  B.1 records occupy pages [start_idx, end_idx).
    """
    start_idx: int | None = None

    for i, page in enumerate(pages):
        for tb in page.get("textboxhorizontal", []):
            text = tb["text"].strip()
            x0 = tb["x0"]

            # Centre-gutter headings sit at x0 between 250 and 320.
            if not (250.0 < x0 < 320.0):
                continue

            if start_idx is None:
                if re.match(r"^B\.1\.?$", text):
                    start_idx = i
            else:
                # Any higher B subsection (B.2, B.3 …) ends B.1.
                if re.match(r"^B\.[2-9]\.?$", text):
                    return start_idx, i

    if start_idx is None:
        raise ValueError("Section B.1 not found in the input file.")

    return start_idx, len(pages)


# ---------------------------------------------------------------------------
# Record extraction
# ---------------------------------------------------------------------------

def extract_records(pages: list, start_idx: int, end_idx: int) -> list:
    """
    Build a list of trademark record dicts from the B.1 page range.

    Reading order (newspaper layout):
      For each page: left column (top→bottom) THEN right column (top→bottom).

    A record begins when INID code 111 is encountered and accumulates
    subsequent INIDs until the next 111 (or end of section).  Records that
    are split across columns or pages are handled transparently because the
    reading order is maintained as a single linear stream.
    """
    records: list = []
    current: dict | None = None

    for page_data in pages[start_idx:end_idx]:
        page_num: int = page_data["page"]
        elements: list = page_data.get("textboxhorizontal", [])

        # Keep only elements in the data area.
        data_els = [tb for tb in elements if is_data_element(tb)]

        # Assign each element to its column.
        left_col = [tb for tb in data_els if tb["x0"] < COLUMN_SPLIT_X]
        right_col = [tb for tb in data_els if tb["x0"] >= COLUMN_SPLIT_X]

        # Process left column first, then right column.
        for col in (left_col, right_col):
            line_groups = group_by_line(col)

            for top_key in sorted(line_groups):
                line = line_groups[top_key]

                # Split the line into INID labels and value text boxes.
                inids = [
                    tb for tb in line
                    if is_inid_code(clean(tb["text"]), tb["x0"])
                ]
                values = [
                    tb for tb in line
                    if not is_inid_code(clean(tb["text"]), tb["x0"])
                ]

                # A line with no INID code carries no structured data.
                if not inids or not values:
                    continue

                # Join multiple value boxes left-to-right (space-separated).
                value_text = " ".join(
                    clean(tb["text"])
                    for tb in sorted(values, key=lambda e: e["x0"])
                ).strip()

                if not value_text:
                    continue

                # Apply each INID on this line (virtually always exactly one).
                for tb in inids:
                    inid = clean(tb["text"])

                    if inid == "111":
                        # Flush the previous record and start a new one.
                        if current is not None:
                            records.append(current)
                        current = {"_PAGE": page_num, "111": value_text}

                    elif current is not None:
                        # Field 400 accumulates as a list; all others are str.
                        if inid in LIST_FIELDS:
                            current.setdefault(inid, []).append(value_text)
                        else:
                            current[inid] = value_text

    # Flush the final record.
    if current is not None:
        records.append(current)

    return records


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        pages = json.load(f)

    start_idx, end_idx = find_b1_bounds(pages)
    print(
        f"Section B.1: pages {pages[start_idx]['page']}–{pages[end_idx - 1]['page']} "
        f"(array indices {start_idx}–{end_idx - 1})"
    )

    records = extract_records(pages, start_idx, end_idx)
    print(f"Extracted {len(records)} trademark records.")

    if records:
        print(f"First record: {records[0]}")
        print(f"Last record:  {records[-1]}")

    output = {"B": {"1": records}}

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Output written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
