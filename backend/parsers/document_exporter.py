# -*- coding: utf-8 -*-
"""Single entry point for exporting translated segments to a real file
(spec section 10). Picks the right writer and enforces which export
formats make sense for a given source format.

Excel sources are purely tabular, so they export cleanly to any format.
Word/PDF sources mix free-flowing prose with tables, and prose has no
natural row/column home in a spreadsheet, so exporting them as Excel would
silently distort the document rather than faithfully represent it. That's
the actual reason for the restriction, not an arbitrary limitation.
"""

from parsers.word_writer import write_docx
from parsers.excel_writer import write_xlsx
from parsers.pdf_writer import write_pdf

EXPORT_COMPATIBILITY = {
    "xlsx": {"docx", "xlsx", "pdf"},
    "docx": {"docx", "pdf"},
    "pdf": {"docx", "pdf"},
}

_WRITERS = {
    "docx": write_docx,
    "xlsx": write_xlsx,
    "pdf": write_pdf,
}


def export_document(segments: list[dict], source_format: str, target_format: str, output_path: str, language: str = None) -> str:
    allowed = EXPORT_COMPATIBILITY.get(source_format)
    if allowed is None:
        raise ValueError(f"Unknown source format '{source_format}'")
    if target_format not in allowed:
        raise ValueError(
            f"Cannot export a {source_format} document as {target_format}. "
            f"A {source_format} source can only be exported as: {', '.join(sorted(allowed))}. "
            f"{'Prose content has no row/column structure to place in a spreadsheet.' if target_format == 'xlsx' else ''}"
        )

    writer = _WRITERS[target_format]
    if target_format == "xlsx":
        return writer(segments, output_path)
    return writer(segments, output_path, language=language)
