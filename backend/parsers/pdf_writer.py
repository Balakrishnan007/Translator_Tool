# -*- coding: utf-8 -*-
"""Writes translated segments back out as a real .pdf file (spec section 10).

Handles segments from any source format: text_block/table_row (native to
PDF), plus paragraph/table_cell (from a Word source being exported as PDF),
plus row (from an Excel source being exported as PDF).
"""

import re

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer

_TABLE_ID_RE = re.compile(r"table(\d+)-row")


def _table_group_key(seg: dict) -> tuple:
    """Groups segments that belong to the same source table. PDF table_row
    ids encode the table index directly; Word table_cell segments already
    carry table_index as a field; Excel rows group by sheet instead."""
    if seg["type"] == "table_row":
        m = _TABLE_ID_RE.search(seg["id"])
        table_idx = m.group(1) if m else "0"
        return ("pdf_table", seg.get("page"), table_idx)
    if seg["type"] == "table_cell":
        return ("word_table", seg["table_index"])
    if seg["type"] == "row":
        return ("excel_sheet", seg.get("sheet"))
    return None


def write_pdf(segments: list[dict], output_path: str, language: str = None) -> str:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output_path, pagesize=A4)
    story = []

    if language:
        story.append(Paragraph(language, styles["Title"]))
        story.append(Spacer(1, 12))

    segments = sorted(segments, key=lambda s: s["order"])

    table_style = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, "black"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ])

    grid_buffer = {}  # table_index -> {row_index: {col_index: text}}
    flat_buffer = []  # list of cell-value lists, for row/table_row
    current_group = None

    def flush():
        nonlocal current_group
        if current_group is None:
            return
        if grid_buffer:
            for key, rows in grid_buffer.items():
                max_col = max(c for row in rows.values() for c in row) if rows else 0
                data = [
                    [rows[r].get(c, "") for c in range(max_col + 1)]
                    for r in sorted(rows)
                ]
                story.append(Table(data, style=table_style))
                story.append(Spacer(1, 8))
            grid_buffer.clear()
        if flat_buffer:
            max_col = max(len(r) for r in flat_buffer)
            data = [row + [""] * (max_col - len(row)) for row in flat_buffer]
            story.append(Table(data, style=table_style))
            story.append(Spacer(1, 8))
            flat_buffer.clear()
        current_group = None

    for seg in segments:
        text = seg.get("translation") or f"[TRANSLATION FAILED: {seg.get('error', 'unknown error')}]"
        seg_type = seg.get("type")
        group = _table_group_key(seg)

        if group is not None:
            if current_group is not None and current_group != group:
                flush()
            current_group = group

        if seg_type == "table_cell":
            grid_buffer.setdefault(seg["table_index"], {}).setdefault(seg["row_index"], {})[seg["col_index"]] = text

        elif seg_type in ("row", "table_row"):
            original_cells = seg.get("cells") or []
            parts = [p.strip() for p in text.split(" | ")]
            if original_cells and len(parts) != len(original_cells):
                parts = [text]
            flat_buffer.append(parts)

        else:  # paragraph, text_block, or anything else: plain text
            flush()
            story.append(Paragraph(text, styles["Normal"]))
            story.append(Spacer(1, 6))

    flush()
    doc.build(story)
    return output_path
