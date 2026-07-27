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


def parse_pdf(file_path: str) -> list[dict]:
    segments = []
    order = 0

    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.find_tables()
            table_bboxes = [t.bbox for t in tables]

            # --- Tables: row-level chunks, matching the Excel decision ---
            for t_idx, table in enumerate(tables):
                rows = table.extract()
                for r_idx, row in enumerate(rows):
                    cells = [(c.strip() if c else "") for c in row]
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

            # --- Everything else: paragraph-like text, tables excluded ---
            def not_in_table(obj, bboxes=table_bboxes):
                for x0, top, x1, bottom in bboxes:
                    if obj["x0"] >= x0 and obj["x1"] <= x1 and obj["top"] >= top and obj["bottom"] <= bottom:
                        return False
                return True

            text_page = page.filter(not_in_table) if table_bboxes else page
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
