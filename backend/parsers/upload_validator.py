# -*- coding: utf-8 -*-
"""Validates an uploaded file before it's trusted enough to parse.

Checks, in order (fail fast on the first problem):
1. Extension is one of the supported formats
2. File isn't empty
3. File isn't absurdly large
4. Actual file signature matches the claimed type (an image renamed to .docx
   should not pass just because of its extension)
5. For docx/xlsx specifically: the zip internally contains the right OOXML
   part (word/document.xml or xl/workbook.xml), since both formats are zip
   files, so the raw signature alone can't tell them apart
6. The file can actually be opened by the real parser without raising
7. The parser actually extracted at least one non-empty segment (catches a
   structurally valid but text-empty document, e.g. a scanned/image-only PDF)
"""

import os
import zipfile

MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
ALLOWED_EXTENSIONS = {".docx", ".xlsx", ".pdf"}

SIGNATURES = {
    ".docx": b"PK\x03\x04",
    ".xlsx": b"PK\x03\x04",
    ".pdf": b"%PDF",
}

OOXML_MARKER = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
}


def validate_upload(file_path: str) -> dict:
    filename = os.path.basename(file_path)
    ext = os.path.splitext(filename)[1].lower()

    # 1. Extension
    if ext not in ALLOWED_EXTENSIONS:
        return _fail(f"Unsupported file type '{ext}'. Allowed: .docx, .xlsx, .pdf")

    # 2. Not empty
    size = os.path.getsize(file_path)
    if size == 0:
        return _fail("File is empty (0 bytes).")

    # 3. Not absurdly large
    if size > MAX_SIZE_BYTES:
        return _fail(f"File is too large ({size / 1024 / 1024:.1f} MB, max {MAX_SIZE_BYTES / 1024 / 1024:.0f} MB).")

    # 4. Real signature matches claimed extension
    with open(file_path, "rb") as f:
        header = f.read(8)
    expected_sig = SIGNATURES[ext]
    if not header.startswith(expected_sig):
        return _fail(
            f"File content doesn't match a valid {ext} file "
            f"(this usually means the file was renamed, not actually converted, "
            f"e.g. an image or other file type given a .{ext.strip('.')} extension)."
        )

    # 5. For docx/xlsx: confirm the internal OOXML structure, not just "it's a zip"
    if ext in OOXML_MARKER:
        try:
            with zipfile.ZipFile(file_path) as z:
                names = z.namelist()
                if OOXML_MARKER[ext] not in names:
                    return _fail(
                        f"File is a valid zip archive but not a real {ext} document "
                        f"(missing {OOXML_MARKER[ext]}, which can happen if a .zip or "
                        f"other archive was renamed to {ext})."
                    )
        except zipfile.BadZipFile:
            return _fail(f"File claims to be {ext} but is not a valid zip archive (corrupted file).")

    # 6 & 7. Actually parse it and confirm it yields real content
    try:
        segments = _parse(file_path, ext)
    except Exception as e:
        return _fail(f"File could not be parsed (corrupted or unreadable {ext} file): {e}")

    if not segments:
        return _fail(
            "No extractable text was found in this file. If this is a PDF, it may be a "
            "scanned/image-only document with no real text layer, which would need OCR "
            "that isn't supported yet."
        )

    return {"valid": True, "error": None, "segment_count": len(segments), "segments": segments}


def _parse(file_path, ext):
    if ext == ".docx":
        from .word_parser import parse_docx
        return parse_docx(file_path)
    if ext == ".xlsx":
        from .excel_parser import parse_xlsx
        return parse_xlsx(file_path)
    if ext == ".pdf":
        from .pdf_parser import parse_pdf
        return parse_pdf(file_path)


def _fail(reason: str) -> dict:
    return {"valid": False, "error": reason, "segment_count": 0, "segments": []}
