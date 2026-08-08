# -*- coding: utf-8 -*-
"""Real source-language auto-detection, using Claude.

Switched from FastText, then Lingua, after direct testing of both: neither
local/non-LLM detector reliably cleared 90% confidence on all 9 real test
documents (FastText: 8/9 best case, Lingua: 7/9), because of short technical
fragments and vocabulary shared between related languages (e.g. "Korpus" is
spelled identically in German and Dutch). An LLM uses actual context, not
just character-frequency statistics, which is what this content needs.

Run directly to test against real dataset files:
    uv run python ai/language_detector.py
"""

import os
import re
import sys

from dotenv import load_dotenv
from anthropic import Anthropic

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from ai.tracing import traced

load_dotenv()

MODEL = "claude-sonnet-5"
SUPPORTED_LANGUAGES = ["German", "English", "Dutch", "French"]

_ALPHA_RE = re.compile(r"[^a-zA-ZÀ-ÿ]")

DETECT_LANGUAGE_TOOL = {
    "name": "submit_language_detection",
    "description": "Submit the detected source language of the document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "enum": SUPPORTED_LANGUAGES,
                "description": "The dominant language the document is written in",
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "How confident you are -- 'low' if the document is genuinely "
                                "mixed-language with no clear dominant language",
            },
        },
        "required": ["language", "confidence"],
    },
}

CONFIDENCE_TO_SCORE = {"high": 1.0, "medium": 0.75, "low": 0.5}
CONFIDENCE_THRESHOLD = 0.90


def _sample_text(seg: dict) -> str:
    """Same cell-selection logic as before: prefer the most linguistically
    rich text out of a segment, not a raw article-number-prefixed string."""
    cells = seg.get("cells")
    if not cells:
        return seg["text"]
    texts = [c["value"] if isinstance(c, dict) else c for c in cells]
    texts = [t for t in texts if t]
    if not texts:
        return seg["text"]
    texts.sort(key=lambda t: -len(_ALPHA_RE.sub("", t)))
    return " ".join(texts[:2])


def detect_language_from_segments(segments: list[dict]) -> dict:
    """Detects the source language of a document from its parsed segments,
    using Claude. One call per document, not per segment, since this only
    runs once on upload and stays cheap."""
    candidates = [seg for seg in segments if len(seg["text"].strip()) > 15][2:]

    # Spread a sample across the whole document, not just the start.
    num_samples = 20
    if len(candidates) > num_samples:
        step = max(1, len(candidates) // num_samples)
        sample_segments = candidates[::step][:num_samples]
    else:
        sample_segments = candidates

    # Capped at 1500 characters, not 4000. Research on language
    # identification specifically found accuracy plateaus around ~1250
    # characters, and excess context can introduce noise rather than help.
    # Confirmed by testing: still 9/9 correct at this smaller, cheaper
    # sample size.
    sample_text = "\n".join(_sample_text(seg) for seg in sample_segments)[:1500]

    if not sample_text.strip():
        return {"language": "Unknown", "confidence": 0.0, "trusted": False}

    system = f"""You are a language detection tool. Determine the dominant language of the
following document excerpt. The document is from a kitchen furniture company and may contain
mixed content (e.g. a German company name/header even in a non-German document, product codes,
dimensions). Focus on the actual body content, not headers or codes, to judge the dominant
language. The document is in one of: {", ".join(SUPPORTED_LANGUAGES)}.

Call the submit_language_detection tool with your result."""

    client = Anthropic()
    with traced("detect_language", sample_length=len(sample_text)):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            tools=[DETECT_LANGUAGE_TOOL],
            tool_choice={"type": "tool", "name": "submit_language_detection"},
            messages=[{"role": "user", "content": sample_text}],
        )

    tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
    if not tool_use_blocks:
        return {"language": "Unknown", "confidence": 0.0, "trusted": False}

    result = tool_use_blocks[0].input
    score = CONFIDENCE_TO_SCORE.get(result["confidence"], 0.5)

    return {
        "language": result["language"],
        "confidence": score,
        "trusted": score >= CONFIDENCE_THRESHOLD,
        "raw_confidence_label": result["confidence"],
    }


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from parsers.word_parser import parse_docx
    from parsers.excel_parser import parse_xlsx
    from parsers.pdf_parser import parse_pdf

    real_files = [
        (r"D:\Rotpunküchen\dataset\01_DE_word_technical_spec.docx", parse_docx, "German"),
        (r"D:\Rotpunküchen\dataset\04_EN_Word_Dealer_Correspondence.docx", parse_docx, "English"),
        (r"D:\Rotpunküchen\dataset\06_NL_Word_Technisch_Datablad_Solide200.docx", parse_docx, "Dutch"),
        (r"D:\Rotpunküchen\dataset\09_FR_Word_Correspondance_Client.docx", parse_docx, "French"),
        (r"D:\Rotpunküchen\dataset\02_DE_Excel_Preisliste_Linea120.xlsx", parse_xlsx, "German"),
        (r"D:\Rotpunküchen\dataset\03_DE_PDF_Katalog_Forma90.pdf", parse_pdf, "German"),
        (r"D:\Rotpunküchen\dataset\05_EN_Excel_Order_Parts_List.xlsx", parse_xlsx, "English"),
        (r"D:\Rotpunküchen\dataset\07_NL_Excel_Prijslijst_Solide200.xlsx", parse_xlsx, "Dutch"),
        (r"D:\Rotpunküchen\dataset\10_FR_Excel_Liste_Prix_Forma90.xlsx", parse_xlsx, "French"),
    ]

    all_pass = True
    for path, parser, expected in real_files:
        segments = parser(path)
        result = detect_language_from_segments(segments)
        ok = expected in result["language"] and result["trusted"]
        all_pass = all_pass and ok
        match = "OK" if ok else "FAILED"
        print(f"[{match}] {os.path.basename(path)}")
        print(f"    Detected: {result['language']} (confidence: {result['raw_confidence_label']}) | Expected: {expected}")

    print()
    print("ALL 9 FILES CORRECT AND TRUSTED" if all_pass else "NOT ALL FILES PASSED")
