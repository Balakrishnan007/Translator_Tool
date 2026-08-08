# -*- coding: utf-8 -*-
"""Glossary-constrained translation of a single chunk, using the Claude API.

Run directly to test against a real chunk from the dataset:
    uv run python ai/translator.py
"""

import os
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from anthropic import Anthropic

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from ai.glossary_loader import load_glossary
from ai.tracing import traced
from parsers.word_parser import parse_docx

load_dotenv()

LANGUAGE_COLUMN = {
    "German": "Source Term (DE)",
    "English": "English",
    "Dutch": "Dutch",
    "French": "French",
}

SYNONYM_COLUMN = {
    "German": "Synonyms (DE)",
    "English": "Synonyms (EN)",
    "Dutch": "Synonyms (NL)",
    "French": "Synonyms (FR)",
}

MODEL = "claude-sonnet-5"


def build_glossary_context(glossary: list[dict], source_language: str, target_language: str) -> str:
    """Builds the glossary context for a given source-to-target language pair.

    Matches terms against whichever column corresponds to the actual source
    language, not a hardcoded German column. This is what makes glossary
    enforcement work correctly for any source language, not just German.
    """
    source_col = LANGUAGE_COLUMN[source_language]
    target_col = LANGUAGE_COLUMN[target_language]
    synonym_col = SYNONYM_COLUMN.get(source_language)
    entries = []
    for g in glossary:
        source_term = g.get(source_col)
        translation = g.get(target_col)
        if not source_term or not translation:
            continue
        entry = {
            "term": source_term,
            "translation": translation,
            "do_not_translate": (g.get("Do Not Translate") == "Y"),
        }
        # Synonyms are language-specific. This only matters when the entry's
        # synonym column matches the document's actual source language: a
        # French document can never contain a German synonym. Split on comma
        # or semicolon in case one cell lists more than one synonym.
        if synonym_col:
            raw = g.get(synonym_col)
            if raw:
                synonyms = [s.strip() for s in re.split(r"[,;]", raw) if s.strip()]
                if synonyms:
                    entry["synonyms"] = synonyms
        entries.append(entry)
    return json.dumps(entries, ensure_ascii=False)


def _attach_glossary_entries(terms: list[dict], glossary: list[dict]) -> None:
    """Fills in the full glossary row for every "glossary"-category term, by
    exact-match lookup against the glossary already loaded in memory. This is
    not something the model needs to regenerate: it can't hallucinate a plain
    lookup, and it costs zero extra tokens compared to asking the AI to repeat
    data we already have on disk (spec section 5: clicking a term shows its
    "Glossareintrag", the glossary entry).

    Skips anything not shaped as expected instead of raising. Confirmed real:
    the model's structured "terms" array occasionally contains a malformed
    item despite the schema, and this function is pure enrichment on top of
    an already-successful translation. It must never be able to throw the
    whole translation away. That's exactly what happened before this fix,
    when a working translation was discarded and replaced with a raw Python
    error string in the rendered document."""
    by_term = {}
    for g in glossary:
        if not isinstance(g, dict):
            continue
        for col in LANGUAGE_COLUMN.values():
            value = g.get(col)
            if value:
                by_term.setdefault(value, g)

    for t in terms:
        if not isinstance(t, dict):
            continue
        if t.get("category") == "glossary":
            entry = by_term.get(t.get("term"))
            if entry:
                t["glossary_entry"] = entry


# Schema for a glossary-aware translation call. Claude must return both the
# translation and a full breakdown of every notable term it noticed, forced
# via tool calling rather than left as free text.
TRANSLATE_WITH_GLOSSARY_TOOL = {
    "name": "submit_translation",
    "description": "Submit the completed translation and its term classification.",
    "input_schema": {
        "type": "object",
        "properties": {
            "translation": {"type": "string", "description": "The translated text"},
            "terms": {
                "type": "array",
                "description": "Every notable term encountered in the source text (spec section 5 categories)",
                "items": {
                    "type": "object",
                    "properties": {
                        "term": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": ["glossary", "protected", "kitchen_technical", "ambiguous", "unknown"],
                        },
                        "used_translation": {"type": "string"},
                        "alternative_translations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "0-3 other reasonable translations for this term, if any exist",
                        },
                        "description": {
                            "type": "string",
                            "description": "One short sentence: what the term means, or why it's flagged in this category",
                        },
                    },
                    "required": ["term", "category", "used_translation", "alternative_translations", "description"],
                },
            },
        },
        "required": ["translation", "terms"],
    },
}

# Schema for a plain, no-glossary translation call: just the text, no term
# breakdown. Used for the with/without glossary comparison.
TRANSLATE_PLAIN_TOOL = {
    "name": "submit_translation",
    "description": "Submit the completed translation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "translation": {"type": "string", "description": "The translated text"},
        },
        "required": ["translation"],
    },
}


SUSPICIOUS_OUTPUTS = {"<UNKNOWN>", "UNKNOWN", "N/A", "[UNKNOWN]", "UNDEFINED", ""}
MAX_RETRIES = 2


def _looks_suspicious(translation: str) -> bool:
    """Catches placeholder-like nonsense a model occasionally returns instead
    of an actual translation. Confirmed real, not hypothetical: a live run
    returned literal "<UNKNOWN>" for a table cell that should have said "900",
    even though a retry of the exact same input translated it correctly."""
    return translation.strip().upper() in SUSPICIOUS_OUTPUTS


def translate_segment(
    text: str,
    source_language: str,
    target_language: str,
    glossary: list[dict],
    use_glossary: bool = True,
    context: str = None,
) -> dict:
    """Translates one segment, retrying automatically if the model returns an
    obviously broken result: empty, or a placeholder like "<UNKNOWN>".

    `context` is optional background info about where this piece of text came
    from, for example "this is the Width column, row also contains: SD200-M90,
    720, 560" for a table cell. Confirmed necessary by testing: translating a
    bare, context-free fragment like "900" in isolation produced a fabricated,
    wrong result, because the model had nothing to anchor it as a dimension.
    """
    last_result = None
    for attempt in range(1, MAX_RETRIES + 2):  # first try, then MAX_RETRIES retries
        last_result = _translate_segment_once(text, source_language, target_language, glossary, use_glossary, context)
        malformed_terms = last_result.pop("_terms_malformed", False)
        if not _looks_suspicious(last_result["translation"]) and not malformed_terms:
            return last_result
    # All attempts looked suspicious. Return the last one anyway, but flag it
    # so callers and reviewers can see it wasn't silently trusted.
    last_result["suspicious"] = True
    return last_result


_URL_RE = re.compile(r"(?:https?://|www\.)\S+")


def _protect_urls(text: str) -> tuple[str, dict]:
    """Swaps URLs for placeholder tokens before sending to the model.

    Confirmed real, not hypothetical: the model spontaneously invented
    markdown link formatting around a plain URL that had no brackets in the
    source at all, and while reproducing that self-invented formatting,
    corrupted the URL itself (a stray newline and bullet got embedded inside
    it). Placeholders can't be mangled, reformatted, or corrupted, since the
    model never sees or has to reproduce the actual URL text. It only sees a
    short opaque token."""
    placeholders = {}

    def replace(match):
        token = f"__URL_{len(placeholders)}__"
        placeholders[token] = match.group(0)
        return token

    return _URL_RE.sub(replace, text), placeholders


def _restore_urls(text: str, placeholders: dict) -> str:
    for token, original in placeholders.items():
        text = text.replace(token, original)
    return text


def _translate_segment_once(
    text: str,
    source_language: str,
    target_language: str,
    glossary: list[dict],
    use_glossary: bool = True,
    context: str = None,
) -> dict:
    # Prompt caching is a strict prefix match: system is rendered before
    # messages, so the per-segment "context" (row/column info) must NOT be
    # interpolated into `system`. It used to be, which meant `system` differed
    # on every single call and could never actually be reused, even with a
    # cache_control marker. It's a plain string here, identical across every
    # segment call for a given (source_language, target_language) pair,
    # exactly what caching needs, with the volatile context moved into the
    # per-call user turn instead, after the cache breakpoint.
    if use_glossary:
        glossary_json = build_glossary_context(glossary, source_language, target_language)
        system_text = f"""You are a professional technical translator for Rotpunkt Kuechen, a German kitchen furniture manufacturer.
Translate the given {source_language} text into {target_language}.

Rules:
- The text may contain tokens like "__URL_0__" -- these are placeholders standing in for a URL. Leave them completely unchanged, exactly as written, wherever they appear. Do not translate them, reformat them, wrap them in markdown or brackets, or alter them in any way.
- For any word or phrase that matches a glossary term below, you MUST use the exact "translation" given -- do not improvise an alternative.
- Some glossary entries also list "synonyms" -- other words that mean the same thing. If the source text uses a synonym instead of the main term, treat it exactly like a match: use the entry's mandated "translation", and classify it under category "glossary" (not "kitchen_technical" or "unknown"). Report the term as it actually appears in the source text, not the glossary's main form.
- Apply normal {target_language} sentence case to glossary translations when you use them in a sentence (e.g. lowercase common nouns like "carcass" or "fronts" unless they start a sentence). The glossary's "translation" field shows the base word, not a required capitalization.
- Any term with "do_not_translate": true MUST be left completely unchanged in your translation, exactly as it appears in the source text (e.g. product names, article numbers).
- If the text is a bare number, code, or dimension, translate ONLY that value (e.g. a number usually just stays the same number) -- use the context given with the text to understand what it represents, but do not invent a sentence around it.
- After translating, list every notable term you encountered in the source text, classified into exactly one of these 5 categories:
  - "glossary": matched a glossary entry below -- you used its mandated translation
  - "protected": matched a do_not_translate entry -- left unchanged
  - "kitchen_technical": a real kitchen/furniture-industry term you recognize (materials, hardware, construction), not in the glossary, and you're confident in how you translated it. Do NOT use this for terms that are clearly outside the kitchen/furniture domain (e.g. generic IT, business, or HR terminology) even if you recognize them confidently -- put those in "unknown" instead, since this category specifically means "kitchen-technical," not "any technical term."
  - "ambiguous": more than one translation is plausible here and you're not fully certain which fits this context (e.g. a word with several valid meanings, or a glossary synonym that could map to more than one entry)
  - "unknown": anything else you're unsure about, or that might be mistranslated -- not a recognized technical term, not confidently handled, OR a confidently-recognized technical term that's outside the kitchen/furniture domain
- For every term, also give: 0-3 "alternative_translations" you considered but didn't use (empty list if none), and one short "description" sentence -- what the term means, or why you put it in that category.

Call the submit_translation tool with your result.

<glossary>
{glossary_json}
</glossary>"""
        tool = TRANSLATE_WITH_GLOSSARY_TOOL
    else:
        # No glossary: plain translation, no term enforcement, no
        # classification. This is the "before" side of the with/without
        # comparison. Short enough that it won't clear the cacheable-prefix
        # minimum anyway, so no cache_control here: nothing to gain, would
        # just add write overhead.
        system_text = f"""You are a translator. Translate the given {source_language} text into {target_language}.

The text may contain tokens like "__URL_0__" -- these are placeholders standing in for a URL. Leave them completely unchanged, exactly as written, wherever they appear.

Call the submit_translation tool with your result."""
        tool = TRANSLATE_PLAIN_TOOL

    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}] if use_glossary else system_text

    protected_text, url_placeholders = _protect_urls(text)
    context_block = f"Context (do not translate this line, it's just background): {context}\n\n" if context else ""
    user_content = f"{context_block}{protected_text}"

    client = Anthropic()
    with traced(
        "translate_segment",
        source_language=source_language,
        target_language=target_language,
        use_glossary=use_glossary,
        has_context=bool(context),
        text_preview=text[:80],
    ):
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_translation"},
            messages=[{"role": "user", "content": user_content}],
        )

    tool_use_blocks = [block for block in response.content if block.type == "tool_use"]

    if not tool_use_blocks:
        raise RuntimeError(
            f"Model didn't call submit_translation (stop_reason={response.stop_reason!r}). "
            f"This usually means max_tokens was too low to finish thinking and still make the "
            f"call. Try increasing max_tokens."
        )

    result = dict(tool_use_blocks[0].input)  # already validated against the schema by the API
    result.setdefault("terms", [])

    # Confirmed real, not hypothetical: on a live run, 2 of 207 segments came
    # back with `terms` as a raw string containing stray tool-call-like text,
    # for example "\n<parameter name=\"terms\">...", instead of the parsed
    # array the schema requires. That string then broke every downstream
    # consumer expecting a list, both the database write and the API's
    # response model. Coercing to an empty list here contains the damage to
    # one segment. The caller marks it suspicious below so it isn't silently
    # trusted either.
    if not isinstance(result["terms"], list):
        result["terms"] = []
        result["_terms_malformed"] = True

    if url_placeholders:
        result["translation"] = _restore_urls(result.get("translation") or "", url_placeholders)
        for t in result["terms"]:
            if isinstance(t, dict):
                if t.get("term"):
                    t["term"] = _restore_urls(t["term"], url_placeholders)
                if t.get("used_translation"):
                    t["used_translation"] = _restore_urls(t["used_translation"], url_placeholders)

    if use_glossary:
        _attach_glossary_entries(result["terms"], glossary)
    result["_usage"] = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
    }
    return result


def _build_segment_context(seg: dict) -> str:
    """Builds a short background-info string for a segment, if it has one.
    Currently only table cells carry this: column header plus rest of the
    row, since that's the case that was proven to need it. A bare cell like
    "900" translated in isolation produced a fabricated result."""
    if seg.get("type") != "table_cell":
        return None

    parts = []
    if seg.get("column_header"):
        parts.append(f"this is the '{seg['column_header']}' column")
    if seg.get("row_context"):
        parts.append("row also contains: " + ", ".join(seg["row_context"]))

    return "; ".join(parts) if parts else None


def translate_document(
    segments: list[dict],
    source_language: str,
    target_languages: list[str],
    glossary: list[dict],
    use_glossary: bool = True,
    max_workers: int = 8,
) -> dict:
    """Translates every segment into every target language.

    Each (segment, language) pair is one independent call to translate_segment.
    Same prompt template every time, only the target_language parameter
    changes (confirmed: no per-language prompt variants, per the research).
    All pairs run concurrently via a thread pool, since the calls are
    I/O-bound (network) and fully independent of each other: translating to
    French never depends on what happened in the Dutch call.

    Returns: {language: [ {segment_id, order, source_text, translation, terms}
                           or {segment_id, order, source_text, error} on failure ]}
    """
    results = {lang: [None] * len(segments) for lang in target_languages}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for lang in target_languages:
            for idx, seg in enumerate(segments):
                context = _build_segment_context(seg)
                future = executor.submit(
                    translate_segment, seg["text"], source_language, lang, glossary, use_glossary, context
                )
                future_map[future] = (lang, idx, seg)

        for future in as_completed(future_map):
            lang, idx, seg = future_map[future]
            try:
                result = future.result()
                results[lang][idx] = {
                    **seg,  # keep all original structural fields: type, table_index, row_index, etc.
                    "source_text": seg["text"],
                    "translation": result["translation"],
                    "terms": result.get("terms", []),
                }
            except Exception as e:
                results[lang][idx] = {
                    **seg,
                    "source_text": seg["text"],
                    "error": str(e),
                }

    for lang in target_languages:
        results[lang].sort(key=lambda r: r["order"])

    return results


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

    print("############ WITHOUT GLOSSARY ############")
    without = translate_segment(segment["text"], "German", "English", glossary, use_glossary=False)
    print(without["translation"])
    print(f"(no term classification available without a glossary)")
    print(f"(tokens used: {without['_usage']['input_tokens']} in / {without['_usage']['output_tokens']} out)")
    print()

    print("############ WITH GLOSSARY ############")
    with_g = translate_segment(segment["text"], "German", "English", glossary, use_glossary=True)
    print(with_g["translation"])
    print("Terms detected:")
    for t in with_g["terms"]:
        print(f"  [{t['category']}] {t['term']} -> {t['used_translation']}")
    print(f"(tokens used: {with_g['_usage']['input_tokens']} in / {with_g['_usage']['output_tokens']} out)")

    print("\n\n############ MULTI-LANGUAGE ORCHESTRATOR ############")
    print("Translating the first 5 real segments into English, Dutch, and French, concurrently...\n")

    real_segments = [s for s in segments if s["text"].strip()][:5]
    target_languages = ["English", "Dutch", "French"]

    doc_results = translate_document(real_segments, "German", target_languages, glossary)

    for lang in target_languages:
        print(f"--- {lang} ---")
        for r in doc_results[lang]:
            if "error" in r:
                print(f"  [{r['id']}] ERROR: {r['error']}")
            else:
                print(f"  [{r['id']}] {r['translation'][:100]}")
        print()

    print("\n############ PROMPT CACHING CHECK ############")
    print("Same (German to English) system prompt, 3 calls in a row. The first")
    print("should show cache_creation_input_tokens > 0 (writes the cache), the")
    print("rest should show cache_read_input_tokens > 0 (reads it back cheaply).\n")
    for i, seg in enumerate(real_segments[:3], start=1):
        r = translate_segment(seg["text"], "German", "English", glossary, use_glossary=True)
        u = r["_usage"]
        print(f"  call {i}: input={u['input_tokens']} cache_write={u['cache_creation_input_tokens']} cache_read={u['cache_read_input_tokens']}")

    from ai.tracing import flush_tracing
    flush_tracing()
