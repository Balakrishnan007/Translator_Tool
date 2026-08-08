# -*- coding: utf-8 -*-
"""Deterministic post-translation quality checks (spec section 8: Quality Check).

Runs after translate_document() returns, over segments that already carry
source_text/translation/terms. No new LLM calls here: every check is plain
Python over data already produced, catching the specific failure mode raw
LLM output has, fluent per-segment translations that silently contradict
each other across a document. Each check below has a real, confirmed example
found in this project's own test runs (see each function's docstring).

Run directly against a synthetic case mirroring the real bugs found:
    uv run python ai/quality_check.py
"""

import re

LANGUAGE_COLUMN = {
    "German": "Source Term (DE)",
    "English": "English",
    "Dutch": "Dutch",
    "French": "French",
}

_CODE_RE = re.compile(r"\b[A-Z0-9]+(?:-[A-Z0-9]+)+\b")
_LETTERS_RE = re.compile(r"[^a-zA-ZÀ-ÿ]")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _find_contradictory_translations(segments: list[dict]) -> list[dict]:
    """Same source term translated to genuinely different words across the
    document. Case-only differences are expected (the system prompt applies
    normal sentence case) and not flagged, only distinct words are.
    Confirmed real: "Façade" -> Front / frontpaneel / fronten in the same
    document; "Système Génération 8" rendered 4 different ways across
    otherwise-identical rows."""
    by_term = {}
    for seg in segments:
        for t in seg.get("terms", []):
            term = t["term"]
            translation = t["used_translation"]
            by_term.setdefault(term, {}).setdefault(translation.lower(), set()).add(translation)

    warnings = []
    for term, variants in by_term.items():
        if len(variants) > 1:
            shown = sorted({v for vs in variants.values() for v in vs})
            warnings.append({
                "type": "contradictory_translation",
                "message": f'"{term}" was translated {len(variants)} different ways: {", ".join(shown)}',
            })
    return warnings


def _find_formatting_problems(segments: list[dict]) -> list[dict]:
    """Number formatting (decimal separator) drifting inconsistently within
    one document. Confirmed real: 6 of 106 prices in a translated price list
    silently switched from period- to comma-decimal while the rest didn't.
    Found by comparing against the source file's native (float) values."""

    def style(token: str):
        if re.fullmatch(r"\d+,\d{1,2}", token):
            return "comma"
        if re.fullmatch(r"\d+\.\d{1,2}", token):
            return "period"
        return None

    tally = {"comma": 0, "period": 0}
    per_segment_styles = []
    for seg in segments:
        translation = seg.get("translation") or ""
        styles = [s for s in (style(tok) for tok in _NUMBER_RE.findall(translation)) if s]
        per_segment_styles.append((seg, styles))
        for s in styles:
            tally[s] += 1

    if tally["comma"] == 0 or tally["period"] == 0:
        return []  # document is already internally consistent, nothing to compare against

    dominant = "comma" if tally["comma"] > tally["period"] else "period"
    minority = "period" if dominant == "comma" else "comma"

    warnings = []
    for seg, styles in per_segment_styles:
        if minority in styles:
            warnings.append({
                "type": "formatting_problem",
                "message": f'Number format inconsistent with the rest of the document '
                           f'(id={seg.get("id")}): "{seg.get("translation")}"',
            })
    return warnings


def _find_untranslated_text(segments: list[dict], glossary: list[dict], source_language: str) -> list[dict]:
    """Segments whose translation is identical to the source, excluding
    protected/do-not-translate terms, product codes, and content that's
    legitimately not prose (bare numbers, article codes)."""
    source_col = LANGUAGE_COLUMN.get(source_language)
    protected = {
        g[source_col] for g in glossary
        if g.get("Do Not Translate") == "Y" and g.get(source_col)
    } if source_col else set()

    warnings = []
    for seg in segments:
        source_text = seg.get("source_text") or ""
        translation = seg.get("translation") or ""
        if not source_text.strip() or source_text.strip() != translation.strip():
            continue

        remainder = source_text
        for term in protected:
            remainder = remainder.replace(term, "")
        remainder = _CODE_RE.sub("", remainder)
        letters = _LETTERS_RE.sub("", remainder)
        if len(letters) >= 4:
            warnings.append({
                "type": "untranslated_text",
                "message": f'Segment id={seg.get("id")} looks untranslated (identical to source): "{source_text[:80]}"',
            })
    return warnings


def _find_unknown_terms(segments: list[dict]) -> list[dict]:
    """Terms the model itself flagged as domain-specific with no glossary
    match. Surfaced here as an actionable warning, not just neutral info:
    this data already exists in `terms`, same as the highlighting summary,
    just re-framed as something that needs a decision, not FYI."""
    seen = set()
    warnings = []
    for seg in segments:
        for t in seg.get("terms", []):
            if t["category"] != "unknown":
                continue
            key = (t["term"], t["used_translation"])
            if key in seen:
                continue
            seen.add(key)
            warnings.append({
                "type": "unknown_term",
                "message": f'"{t["term"]}" -> "{t["used_translation"]}" has no glossary entry',
            })
    return warnings


def _find_missing_glossary_terms(segments: list[dict], glossary: list[dict], source_language: str) -> list[dict]:
    """Glossary terms present in a segment's source text that the model's
    own term list never reports applying. Catches silent skips, not just
    wrong output: the model translated it, just not necessarily the way the
    glossary mandates, without saying so."""
    source_col = LANGUAGE_COLUMN.get(source_language)
    if not source_col:
        return []

    glossary_terms = [g[source_col] for g in glossary if g.get(source_col)]

    warnings = []
    for seg in segments:
        source_text = seg.get("source_text") or ""
        if not source_text:
            continue
        # "protected" counts as applied too. A do_not_translate glossary
        # entry is correctly reported under category "protected", not
        # "glossary". Confirmed real: without this check, every correctly
        # untranslated product/company name (e.g. "Forma 90") was wrongly
        # flagged as a missed glossary term on a live run.
        applied = {t["term"] for t in seg.get("terms", []) if t["category"] in ("glossary", "protected")}
        for term in glossary_terms:
            if term in applied:
                continue
            if re.search(rf"\b{re.escape(term)}\b", source_text, re.IGNORECASE):
                warnings.append({
                    "type": "missing_glossary_term",
                    "message": f'Segment id={seg.get("id")} contains glossary term "{term}" but it was not applied',
                })
    return warnings


def run_quality_check(segments: list[dict], glossary: list[dict], source_language: str) -> list[dict]:
    """Runs every check (spec section 8) over a completed translation.
    Returns a flat list of warning dicts, most-impactful categories first."""
    warnings = []
    warnings += _find_contradictory_translations(segments)
    warnings += _find_formatting_problems(segments)
    warnings += _find_untranslated_text(segments, glossary, source_language)
    warnings += _find_missing_glossary_terms(segments, glossary, source_language)
    warnings += _find_unknown_terms(segments)
    return warnings


if __name__ == "__main__":
    # Synthetic case mirroring the exact real bugs found in this project's
    # own test runs, so the checks can be verified without spending API
    # calls or depending on LLM output being reproducible run to run.
    fake_glossary = [
        {"Source Term (DE)": "Front", "English": "front", "Dutch": "Front", "French": "Façade", "Do Not Translate": "N"},
        {"Source Term (DE)": "Korpus", "English": "carcass", "Dutch": "Korpus", "French": "Caisson", "Do Not Translate": "N"},
        {"Source Term (DE)": "Rotpunkt Küchen GmbH", "English": "Rotpunkt Küchen GmbH", "Dutch": "Rotpunkt Küchen GmbH", "French": "Rotpunkt Küchen GmbH", "Do Not Translate": "Y"},
    ]

    fake_segments = [
        {"id": "s1", "source_text": "La façade est en MDF.", "translation": "Het front is van MDF.",
         "terms": [{"term": "Façade", "category": "glossary", "used_translation": "Front"}]},
        {"id": "s2", "source_text": "Façade avec poignée.", "translation": "Fronten met greep.",
         "terms": [{"term": "Façade", "category": "glossary", "used_translation": "fronten"}]},
        {"id": "s3", "source_text": "Prix: 102,78", "translation": "Prijs: 102,78", "terms": []},
        {"id": "s4", "source_text": "Prix: 85.8", "translation": "Prijs: 85.8", "terms": []},
        {"id": "s5", "source_text": "Prix: 46.63", "translation": "Prijs: 46.63", "terms": []},
        {"id": "s6", "source_text": "Livraison sous 8 semaines.", "translation": "Livraison sous 8 semaines.", "terms": []},
        {"id": "s7", "source_text": "Le caisson est en bois.", "translation": "De korpus is van hout.",
         "terms": []},  # "Korpus" glossary term present in source, never reported as applied
    ]

    warnings = run_quality_check(fake_segments, fake_glossary, "French")
    print(f"{len(warnings)} warnings found on the synthetic test case:\n")
    for w in warnings:
        print(f"  [{w['type']}] {w['message']}")
