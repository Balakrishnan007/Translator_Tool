# -*- coding: utf-8 -*-
"""Glossary-constrained translation of a single chunk, using the Claude API.

Run directly to test against a real chunk from the dataset:
    uv run python ai/translator.py
"""

import os
import json
import sys

from dotenv import load_dotenv
from anthropic import Anthropic

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from ai.glossary_loader import load_glossary
from parsers.word_parser import parse_docx

load_dotenv()

LANGUAGE_COLUMN = {
    "English": "English",
    "Dutch": "Dutch",
    "French": "French",
}

MODEL = "claude-sonnet-5"


def build_glossary_context(glossary: list[dict], target_language: str) -> str:
    col = LANGUAGE_COLUMN[target_language]
    entries = []
    for g in glossary:
        translation = g.get(col)
        if not translation:
            continue
        entries.append({
            "term": g["Source Term (DE)"],
            "translation": translation,
            "do_not_translate": (g.get("Do Not Translate") == "Y"),
        })
    return json.dumps(entries, ensure_ascii=False)


def translate_segment(text: str, source_language: str, target_language: str, glossary: list[dict]) -> dict:
    glossary_json = build_glossary_context(glossary, target_language)

    system = f"""You are a professional technical translator for Rotpunkt Kuechen, a German kitchen furniture manufacturer.
Translate the given {source_language} text into {target_language}.

Rules:
- For any word or phrase that matches a glossary term below, you MUST use the exact "translation" given -- do not improvise an alternative.
- Apply normal {target_language} sentence case to glossary translations when you use them in a sentence (e.g. lowercase common nouns like "carcass" or "fronts" unless they start a sentence). The glossary's "translation" field shows the base word, not a required capitalization.
- Any term with "do_not_translate": true MUST be left completely unchanged in your translation, exactly as it appears in the source text (e.g. product names, article numbers).
- After translating, list every glossary/protected/unknown term you encountered in the source text.
  - category "glossary": matched a glossary entry
  - category "protected": matched a do_not_translate entry
  - category "unknown": a domain-specific / technical term with no glossary match at all

Respond with ONLY valid JSON, no other text, in exactly this shape:
{{"translation": "...", "terms": [{{"term": "...", "category": "glossary|protected|unknown", "used_translation": "..."}}]}}

<glossary>
{glossary_json}
</glossary>"""

    client = Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": text}],
    )

    text_blocks = [block.text for block in response.content if block.type == "text"]
    raw = "".join(text_blocks)
    result = json.loads(raw)
    result["_usage"] = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return result


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not found. Check that backend/.env exists and contains it.")
        sys.exit(1)

    glossary = load_glossary()
    segments = parse_docx(r"D:\Rotpunküchen\dataset\01_DE_word_technical_spec.docx")
    segment = next(s for s in segments if s["id"] == "p2")

    print("=== Source (German) ===")
    print(segment["text"])
    print()

    result = translate_segment(segment["text"], "German", "English", glossary)

    print("=== Translation (English) ===")
    print(result["translation"])
    print()

    print("=== Terms detected ===")
    for t in result["terms"]:
        print(f"  [{t['category']}] {t['term']} -> {t['used_translation']}")

    print(f"\n(tokens used: {result['_usage']['input_tokens']} in / {result['_usage']['output_tokens']} out)")
