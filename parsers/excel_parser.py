# -*- coding: utf-8 -*-
"""Parses an .xlsx file into an ordered list of translatable row-level segments.

Row-level, not cell-level: a lone cell like "50€" or "Korpus" has no context
on its own, but the full row (article number + name + material + price) gives
the model everything it needs to translate correctly. Each cell is still kept
individually addressable (in "cells") so translations can be written back into
the correct column on export.
"""

import openpyxl


def parse_xlsx(file_path: str) -> list[dict]:
    wb = openpyxl.load_workbook(file_path, data_only=True)
    segments = []
    order = 0

    for ws in wb.worksheets:
        header_cells = None
        for row in ws.iter_rows():
            cells = [(str(c.value).strip() if c.value is not None else "") for c in row]
            if not any(cells):
                continue

            row_idx = row[0].row
            is_header = row_idx == 1
            if is_header:
                header_cells = cells

            segments.append({
                "id": f"{ws.title}!row{row_idx}",
                "type": "row",
                "order": order,
                "text": " | ".join(c for c in cells if c),
                "sheet": ws.title,
                "row_index": row_idx,
                "is_header": is_header,
                "cells": [
                    {"col_index": i + 1, "header": (header_cells[i] if header_cells and i < len(header_cells) else None), "value": c}
                    for i, c in enumerate(cells) if c
                ],
            })
            order += 1

    return segments


if __name__ == "__main__":
    import sys
    import json

    path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Rotpunküchen\dataset\02_DE_Excel_Preisliste_Linea120.xlsx"
    result = parse_xlsx(path)
    print(f"Parsed {len(result)} row segments from {path}\n")
    for seg in result[:5]:
        print(json.dumps(seg, ensure_ascii=False))
