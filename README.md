# Rotpunkt Translation Tool — Prototype

An AI-powered document translation prototype for the kitchen industry, built as a technical
demonstration for an AI Solutions Engineer application. Independent prototype — not an official
Rotpunkt Küchen project.

## What it does

Translates kitchen-industry business documents (Word, Excel, PDF) while enforcing a
company-specific glossary: consistent term translation, protected product names/article
numbers left untranslated, and unknown-term detection.

## Status

Actively in development. Current progress:

- [x] File parsers for `.docx`, `.xlsx`, `.pdf` — extract ordered, addressable text segments
- [x] Upload validation — rejects wrong/corrupted/empty files, confirmed against real edge cases
- [ ] AI core: glossary-constrained translation
- [ ] Term highlighting / classification
- [ ] Quality check pass
- [ ] Backend API + database persistence
- [ ] Frontend

## Stack

- Python, managed with [uv](https://docs.astral.sh/uv/)
- `python-docx`, `openpyxl`, `pdfplumber` for parsing
- `pytest` for testing
- FastAPI + PostgreSQL planned for the backend
- Claude API for the translation/classification core

## Running tests

```bash
uv run pytest -v
```
