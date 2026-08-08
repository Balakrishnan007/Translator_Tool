# -*- coding: utf-8 -*-
"""Single entry point for parsing any supported document.

Detects the file type from its extension, validates it, runs the correct
parser, prints a summary, and saves the resulting chunks to disk as JSON
so they persist and can be inspected, not just printed and discarded.

Usage:
    uv run python scripts/parse_document.py <path-to-file>
"""

import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from parsers.upload_validator import validate_upload
from parsers.word_parser import parse_docx
from parsers.excel_parser import parse_xlsx
from parsers.pdf_parser import parse_pdf

PARSERS = {
    ".docx": parse_docx,
    ".xlsx": parse_xlsx,
    ".pdf": parse_pdf,
}

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "chunks")


def parse_document(file_path: str) -> dict:
    ext = os.path.splitext(file_path)[1].lower()

    if ext not in PARSERS:
        return {"ok": False, "error": f"Unsupported file type '{ext}'. Supported: {', '.join(PARSERS)}"}

    validation = validate_upload(file_path)
    if not validation["valid"]:
        return {"ok": False, "error": validation["error"]}

    chunks = PARSERS[ext](file_path)
    return {"ok": True, "file": file_path, "format": ext, "chunk_count": len(chunks), "chunks": chunks}


def save_chunks(file_path: str, result: dict) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUTPUT_DIR, f"{base_name}_{timestamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return out_path


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python parse_document.py <path-to-file>")
        sys.exit(1)

    file_path = sys.argv[1]
    result = parse_document(file_path)

    if not result["ok"]:
        print(f"REJECTED: {result['error']}")
        sys.exit(1)

    print(f"Parsed: {result['file']}")
    print(f"Format: {result['format']}")
    print(f"Chunks: {result['chunk_count']}\n")

    print("First 5 chunks:")
    for chunk in result["chunks"][:5]:
        preview = chunk["text"][:100]
        print(f"  [{chunk['type']}] {preview}")

    out_path = save_chunks(file_path, result)
    print(f"\nSaved all {result['chunk_count']} chunks to: {out_path}")


if __name__ == "__main__":
    main()
