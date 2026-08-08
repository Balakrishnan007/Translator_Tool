# -*- coding: utf-8 -*-
"""The actual interactive flow, standing in for the frontend until it exists:

  pick a file -> confirm source language (real detection not built yet) ->
  choose target language(s) -> translate all of them -> pick ONE to view ->
  see it rendered in the terminal, tables as tables, not flat text.

Run with: uv run python run_app.py
"""

import os
import sys
import tkinter as tk
from tkinter import filedialog

from rich.console import Console
from rich.table import Table

from parsers.upload_validator import validate_upload
from ai.glossary_loader import load_glossary
from ai.translator import translate_document
from ai.language_detector import detect_language_from_segments
from ai.quality_check import run_quality_check
from ai.tracing import flush_tracing

console = Console()

AVAILABLE_LANGUAGES = ["German", "English", "Dutch", "French"]


def pick_file() -> str:
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.askopenfilename(
        title="Choose a document to translate",
        filetypes=[("Supported documents", "*.docx *.xlsx *.pdf"), ("All files", "*.*")],
    )
    return file_path


def determine_source_language(segments: list[dict]) -> str:
    result = detect_language_from_segments(segments)

    if result["trusted"]:
        console.print(f"\n[green]Detected language: {result['language']} (confidence: {result['raw_confidence_label']})[/green]")
        return result["language"]

    console.print(
        f"\n[yellow]Detected language: {result['language']}, but confidence is only "
        f"'{result['raw_confidence_label']}' (below the trust threshold). Please confirm or correct.[/yellow]"
    )
    typed = input(f"Language is [{result['language']}]? Press Enter to accept, or type the correct one: ").strip()
    return typed or result["language"]


def choose_target_languages(source_language: str) -> list[str]:
    # Never offer the detected source language as a translation target.
    # Translating a document into the language it's already written in
    # doesn't make sense, regardless of which language that turns out to be.
    options = [lang for lang in AVAILABLE_LANGUAGES if lang != source_language]

    console.print("\nAvailable target languages:")
    for i, lang in enumerate(options, start=1):
        console.print(f"  {i}) {lang}")
    raw = input("Select target language(s), comma-separated (e.g. 1,3): ").strip()
    indices = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
    chosen = [options[i - 1] for i in indices if 1 <= i <= len(options)]
    return chosen or [options[0]]


def choose_language_to_view(translated_languages: list[str]) -> str:
    console.print("\nTranslation complete. Which one do you want to view?")
    for i, lang in enumerate(translated_languages, start=1):
        console.print(f"  {i}) {lang}")
    raw = input("Choose one: ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(translated_languages):
        return translated_languages[int(raw) - 1]
    return translated_languages[0]


# Spec section 5's 5 color categories, mapped to the closest rich terminal
# colors. This is the same category-to-color idea a real frontend would
# express as CSS classes on <span> elements; a terminal can't do click
# popups, but it can do the color-coding itself, inline, right now.
CATEGORY_COLOR = {
    "glossary": "green",
    "protected": "white",
    "kitchen_technical": "blue",
    "ambiguous": "orange3",
    "unknown": "red",
}


def _highlight_terms(text: str, terms: list[dict]) -> str:
    """Colors each term's translated form inline within the actual rendered
    text, instead of only listing terms separately afterward. A terminal
    preview of the real span-based highlighting spec section 5 describes.
    Longest translations are wrapped first so a short term (e.g. "Front")
    can't corrupt the markup of a longer one that contains it (e.g. "Fronten")."""
    ordered = sorted(terms, key=lambda t: -len(t.get("used_translation") or ""))
    for t in ordered:
        translation = t.get("used_translation")
        if not translation or translation not in text:
            continue
        color = CATEGORY_COLOR.get(t.get("category"), "white")
        text = text.replace(translation, f"[{color}]{translation}[/{color}]", 1)
    return text


def render_segments(language: str, segments: list[dict]):
    """Renders translated segments in the terminal, respecting structure:
    paragraphs as text, table cells/rows grouped into real rendered tables."""
    console.rule(f"[bold]{language}[/bold]")

    table_buffer = {}  # (table_index) -> {row_index: {col_index: text}}
    current_table_key = None

    def flush_table():
        nonlocal current_table_key
        if current_table_key is None:
            return
        rows = table_buffer.pop(current_table_key)
        max_col = max(c for row in rows.values() for c in row) if rows else 0
        t = Table(show_header=False)
        for _ in range(max_col + 1):
            t.add_column()
        for row_idx in sorted(rows):
            cells = rows[row_idx]
            t.add_row(*[cells.get(c, "") for c in range(max_col + 1)])
        console.print(t)
        current_table_key = None

    all_terms = []  # collected across every segment, for the summary below

    for seg in segments:
        if "translation" in seg:
            text = seg["translation"]
        else:
            # A failed segment must never render as if it were real
            # translated content. Confirmed real: a raw Python exception
            # string once leaked straight into the printed document with no
            # marker, indistinguishable from an actual sentence.
            text = f"[TRANSLATION FAILED: {seg.get('error', 'unknown error')}]"
        seg_type = seg.get("type")
        terms = seg.get("terms", [])
        all_terms.extend(terms)
        if terms and "translation" in seg:
            text = _highlight_terms(text, terms)

        if seg_type == "table_cell":
            key = seg["table_index"]
            if current_table_key is not None and current_table_key != key:
                flush_table()
            current_table_key = key
            table_buffer.setdefault(key, {}).setdefault(seg["row_index"], {})[seg["col_index"]] = text

        elif seg_type in ("row", "table_row"):
            flush_table()
            console.print(f"  {text}")

        else:  # paragraph, text_block, or anything else: plain text
            flush_table()
            console.print(text)

    flush_table()

    _render_terms_summary(all_terms)


def _render_terms_summary(all_terms: list[dict]):
    """Shows what the highlighting feature (spec §5) would color-code. This
    data already existed in every translation call, it just wasn't being
    displayed anywhere until now. Approximates the "click a term" detail
    panel (translation used, alternatives, description, glossary entry) as
    plain text, since a terminal has no clickable elements."""
    if not all_terms:
        return

    # De-duplicate: the same term (e.g. "Korpus") appears once per occurrence
    # in the document. Show it once per category, not dozens of times.
    seen = set()
    by_category = {c: [] for c in CATEGORY_COLOR}
    for t in all_terms:
        key = (t["category"], t["term"], t["used_translation"])
        if key in seen:
            continue
        seen.add(key)
        by_category.setdefault(t["category"], []).append(t)

    console.print()
    console.rule("[bold]Terms detected (§5 highlighting data)[/bold]")
    labels = {
        "protected": "[white]Protected (never translated)[/white]",
        "glossary": "[green]Glossary matches[/green]",
        "kitchen_technical": "[blue]Kitchen technical terms[/blue]",
        "ambiguous": "[orange3]Ambiguous terms[/orange3]",
        "unknown": "[red]Unknown / possibly incorrect terms[/red]",
    }
    for category, label in labels.items():
        items = by_category.get(category, [])
        if not items:
            continue
        console.print(f"\n{label}:")
        for t in items:
            console.print(f"  {t['term']} -> {t['used_translation']}")
            if t.get("description"):
                console.print(f"    [dim]{t['description']}[/dim]")
            alts = t.get("alternative_translations")
            if alts:
                console.print(f"    [dim]alternatives: {', '.join(alts)}[/dim]")
            entry = t.get("glossary_entry")
            if entry:
                console.print(f"    [dim]glossary entry: {entry.get('Category', '')}, do not translate: {entry.get('Do Not Translate', 'N')}[/dim]")


def _render_quality_warnings(warnings: list[dict]):
    """Prints deterministic quality-check warnings (spec section 8) before
    the translated document. Unlike the term summary, which only appears
    at the end and is easy to miss after a long table dump."""
    console.print()
    console.rule("[bold]Quality check[/bold]")
    if not warnings:
        console.print("[green]No issues found.[/green]")
        return

    labels = {
        "contradictory_translation": "[red]Contradictory translation[/red]",
        "formatting_problem": "[red]Formatting problem[/red]",
        "untranslated_text": "[yellow]Possibly untranslated[/yellow]",
        "missing_glossary_term": "[yellow]Missing glossary term[/yellow]",
        "unknown_term": "[yellow]Unknown term[/yellow]",
    }
    by_type = {}
    for w in warnings:
        by_type.setdefault(w["type"], []).append(w)

    for wtype, label in labels.items():
        items = by_type.get(wtype, [])
        if not items:
            continue
        console.print(f"\n{label} ({len(items)}):")
        for w in items:
            console.print(f"  {w['message']}")


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[red]ERROR: ANTHROPIC_API_KEY not found. Check that backend/.env exists.[/red]")
        sys.exit(1)

    file_path = pick_file()
    if not file_path:
        console.print("No file selected.")
        return

    console.print(f"\nSelected: {file_path}")
    validation = validate_upload(file_path)
    if not validation["valid"]:
        console.print(f"[red]REJECTED: {validation['error']}[/red]")
        return

    console.print(f"[green]Accepted: {validation['segment_count']} segments extracted.[/green]")

    source_language = determine_source_language(validation["segments"])
    target_languages = choose_target_languages(source_language)
    console.print(f"\nTranslating into: {', '.join(target_languages)} ...")

    glossary = load_glossary()
    results = translate_document(validation["segments"], source_language, target_languages, glossary)

    view_language = choose_language_to_view(target_languages)
    warnings = run_quality_check(results[view_language], glossary, source_language)
    _render_quality_warnings(warnings)
    render_segments(view_language, results[view_language])

    flush_tracing()


if __name__ == "__main__":
    main()
