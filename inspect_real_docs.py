# -*- coding: utf-8 -*-
"""Inspects real Rotpunkt brochure PDFs: page count, and how much real text
(vs. design-only/outlined text) is actually extractable per page."""

import os
import pdfplumber

FILES = [
    "250902_RP_Innovations-2026_280x375mm_AB_RZ_Ansicht.pdf",
    "ROK-1001-25_BR_Imagebroschuere_R5_DE_Ansicht.pdf",
    "ROK-1009_BR_power_of_possibilities_R4_DE_Ansicht.pdf",
    "coming_home-2023.pdf",
    "less_is_more-2023.pdf",
    "smart_and_bright-2023.pdf",
]

BASE = r"D:\Rotpunküchen"

for fname in FILES:
    path = os.path.join(BASE, fname)
    size_mb = os.path.getsize(path) / 1024 / 1024
    print(f"\n=== {fname} ({size_mb:.1f} MB) ===")
    with pdfplumber.open(path) as pdf:
        num_pages = len(pdf.pages)
        pages_with_text = 0
        total_chars = 0
        sample_text = ""
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages_with_text += 1
                total_chars += len(text)
                if not sample_text and text.strip():
                    sample_text = text.strip()[:200]
        print(f"  Pages: {num_pages}")
        print(f"  Pages with extractable text: {pages_with_text} / {num_pages}")
        print(f"  Total extracted characters: {total_chars}")
        print(f"  Sample text found: {sample_text!r}" if sample_text else "  No extractable text found on any page.")
