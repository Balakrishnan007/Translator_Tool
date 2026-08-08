# -*- coding: utf-8 -*-
"""Parses a .pdf file into an ordered list of translatable segments.

PDF has no reliable structural markup (no paragraph/table tags like docx/xlsx).
Tables are detected and extracted as row-level chunks (matching the Excel
row-level decision, for consistency and context). Everything else is grouped
into paragraph-like text blocks using a sentence-boundary heuristic, with
table regions excluded so table content doesn't also show up flattened into
the plain-text blocks.
"""

import pdfplumber


def _assign_row_text(row_cells: list, words: list) -> list[str]:
    """Builds each cell's text from words, instead of trusting Table.extract().

    Table.extract() clips strictly to each cell's ruled rectangle, which fails
    when a cell's actual text overflows its printed border. Confirmed on a
    real file where a table header ("Univers de couleurs disponibles") was
    typeset past the table's own right-hand ruling line in the source PDF
    itself, so the ruling-based clip truncated it to "...dispo".

    Instead, a word is assigned to whichever column its STARTING x-position
    falls into. That's reliable even when the word's tail overflows the
    column's right edge, since overflow only ever pushes text further right,
    never changes where it starts.
    """
    present = [(i, c) for i, c in enumerate(row_cells) if c]
    if not present:
        return ["" for _ in row_cells]

    row_top = min(c[1] for _, c in present)
    row_bottom = max(c[3] for _, c in present)
    col_starts = [(i, c[0]) for i, c in present]  # (cell index, left edge)

    cells_words = [[] for _ in row_cells]
    for w in words:
        w_center_y = (w["top"] + w["bottom"]) / 2
        if not (row_top - 1 <= w_center_y <= row_bottom + 1):
            continue
        # rightmost column whose left edge is still <= this word's start
        col_idx = max((i for i, x0 in col_starts if x0 <= w["x0"] + 0.5), default=col_starts[0][0])
        cells_words[col_idx].append(w)

    result = []
    for i in range(len(row_cells)):
        ws = sorted(cells_words[i], key=lambda w: w["x0"])
        result.append(" ".join(w["text"] for w in ws))
    return result


def parse_pdf(file_path: str) -> list[dict]:
    segments = []
    order = 0

    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.find_tables()
            table_bboxes = [t.bbox for t in tables]
            words = page.extract_words()

            # Tables: row-level chunks, matching the Excel decision.
            for t_idx, table in enumerate(tables):
                for r_idx, row in enumerate(table.rows):
                    cells = [c.strip() for c in _assign_row_text(row.cells, words)]
                    if not any(cells):
                        continue
                    segments.append({
                        "id": f"page{page_num}-table{t_idx}-row{r_idx}",
                        "type": "table_row",
                        "order": order,
                        "text": " | ".join(c for c in cells if c),
                        "page": page_num,
                        "cells": cells,
                    })
                    order += 1

            # Everything else: paragraph-like text, tables excluded.
            # Excluding by the raw table bbox at char level is unsafe: table
            # detection bboxes don't always align exactly with a line's glyph
            # positions, so a char-level cut can bisect a word. Confirmed on a
            # real file: "disponibles" in a table header got sliced into an
            # orphan "nibles" fragment that leaked into the plain-text stream.
            # Fix: decide exclusion per word (via each word's own bbox, using
            # its center point against the table bbox), then use those whole
            # word boxes, not the raw table box, as the char-level exclude
            # regions. A char's bbox is always fully inside its own word's
            # bbox, so this guarantees a word is either kept whole or dropped
            # whole, never split.
            exclude_bboxes = []
            if table_bboxes:
                for w in words:
                    wx0, wtop, wx1, wbottom = w["x0"], w["top"], w["x1"], w["bottom"]
                    w_center_x = (wx0 + wx1) / 2
                    w_center_y = (wtop + wbottom) / 2
                    for x0, top, x1, bottom in table_bboxes:
                        if x0 <= w_center_x <= x1 and top <= w_center_y <= bottom:
                            exclude_bboxes.append((wx0, wtop, wx1, wbottom))
                            break

            def not_in_table(obj, bboxes=exclude_bboxes):
                for x0, top, x1, bottom in bboxes:
                    if obj["x0"] >= x0 and obj["x1"] <= x1 and obj["top"] >= top and obj["bottom"] <= bottom:
                        return False
                return True

            text_page = page.filter(not_in_table) if exclude_bboxes else page
            text = text_page.extract_text() or ""
            lines = [l.strip() for l in text.split("\n") if l.strip()]

            blocks = []
            current = []
            for line in lines:
                current.append(line)
                if line.endswith((".", ":", "?", "!")) and len(line) < 100:
                    blocks.append(" ".join(current))
                    current = []
            if current:
                blocks.append(" ".join(current))

            for b_idx, block in enumerate(blocks):
                segments.append({
                    "id": f"page{page_num}-b{b_idx}",
                    "type": "text_block",
                    "order": order,
                    "text": block,
                    "page": page_num,
                })
                order += 1

    return segments


if __name__ == "__main__":
    import sys
    import json

    path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Rotpunküchen\dataset\03_DE_PDF_Katalog_Forma90.pdf"
    result = parse_pdf(path)
    table_rows = [s for s in result if s["type"] == "table_row"]
    text_blocks = [s for s in result if s["type"] == "text_block"]
    print(f"Parsed {len(result)} segments from {path}")
    print(f"  -> {len(table_rows)} table rows, {len(text_blocks)} text blocks\n")
    print("First 5 table rows found:")
    for seg in table_rows[:5]:
        print(" ", json.dumps(seg, ensure_ascii=False)[:200])
    print("\nFirst 5 text blocks:")
    for seg in text_blocks[:5]:
        print(" ", json.dumps(seg, ensure_ascii=False)[:200])
