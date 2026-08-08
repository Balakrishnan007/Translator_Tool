# Rotpunkt Küchen — Übersetzungstool (Translation Tool)

An AI-powered document translation prototype built as a technical deliverable for an
**AI Solutions Engineer** interview. It translates real kitchen-industry business documents
(Word, Excel, PDF) while enforcing a company-specific glossary, classifying every notable term
it encounters, running an automated quality check, and requiring human approval before export.

> Independent prototype built for an interview process — not an official Rotpunkt Küchen
> project, and not affiliated with the company.

---

## Table of contents

1. [What it actually does](#what-it-actually-does)
2. [Data preparation — no dataset was provided](#data-preparation--no-dataset-was-provided)
3. [Architecture](#architecture)
4. [How a translation actually works](#how-a-translation-actually-works)
5. [Key decisions, and why](#key-decisions-and-why)
6. [Real bugs found and fixed during development](#real-bugs-found-and-fixed-during-development)
7. [Path to production](#path-to-production)
8. [Project structure](#project-structure)
9. [Running it yourself](#running-it-yourself)
10. [Spec compliance](#spec-compliance)

---

## What it actually does

```
Upload (.docx / .xlsx / .pdf)
        │
        ▼
Parse into segments (paragraph / table row / table cell)
        │
        ▼
Detect source language  ─── 1 AI call, sampled across the whole document
        │
        ▼
Select target language(s)  ─── one or several at once
        │
        ▼
Translate + classify every segment, every language  ─── concurrently, not one at a time
        │
        ▼
Automatic quality check  ─── plain code, catches what parallel AI calls can't see themselves
        │
        ▼
Review: side-by-side view, color-coded terms, click any term for detail
        │
        ▼
Manual correction (optional)
        │
        ▼
Approval  ─── export is locked until this happens
        │
        ▼
Export (Word / Excel / PDF — translation only, bilingual, or a quality report)
```

## Data preparation — no dataset was provided

The task document (§2) specifies exactly which file formats the tool must support — Word,
Excel, PDF, and InDesign — but includes no sample documents. Before any translation logic could
be built or genuinely tested, a realistic dataset had to be created from scratch.

The dataset (`dataset/`) was built to actually exercise the tool, not just demonstrate it:

- All 4 languages the tool targets: German, English, Dutch, French
- All 3 formats that could realistically be produced: Word, Excel, PDF
- Deliberately substantial rather than minimal — one German technical specification document
  runs 8 pages and includes several real tables (pricing, hardware specifications, dimensions),
  since a short, single-paragraph sample would never have exercised the parsing, table-context,
  and quality-check logic the way an actual business document does
- A company glossary spreadsheet, structured to match what the spec describes (§7)
- Content generated with Claude's assistance, from a written brief describing what each
  document needed to contain and why — kept in the dataset folder itself
  (`dataset_brief_for_claude_chat.md`)

**InDesign (`.imdd`) was not created.** Unlike the other 3 formats, `.imdd` is Adobe InDesign's
own proprietary save format — producing a genuine one requires the actual InDesign application,
not something a script or a written description can generate.

## Architecture

Two independently-runnable pieces, talking only over HTTP:

```
Browser (vanilla JS, no framework)
   │
   │  HTTP / JSON
   ▼
FastAPI backend (Python)
   │
   ├──►  PostgreSQL    projects, segments, translations
   │
   ├──►  Claude API    translation, term classification, language detection
   │
   └──►  Langfuse      every real AI call, traced with business context
```

**Backend**, layered — a request only ever flows downward, never sideways or back up into a
layer it already passed through:

```
api/        FastAPI routes
   │
   ▼
db/         SQLAlchemy models + CRUD layer
   │
   ▼
ai/         Claude-based translation, classification, language detection
   │
   ▼
parsers/    Format-specific document extraction and export
```

Routes call CRUD functions, never raw SQL. The AI layer knows nothing about HTTP or the
database. Parsers know nothing about either.

**Frontend**: a single `app.js` driving 6 screens (upload → language selection → translation
status/polling → review → approval → export) against the REST API, with `fetch()` and no
framework. `state` is one in-memory object; every screen is a function that re-renders `#app`.

## How a translation actually works

### Parsing — turning a document into segments

Each file format is structured completely differently, so each has its own parsing logic —
there's no shared parser underneath:

- **Word**: real structure exists in the file format itself, so paragraphs and tables are read
  directly as their own objects.
- **Excel**: also real structure — every row of every worksheet is read directly, with row 1
  treated as headers.
- **PDF**: no structure at all. Tables are detected by their ruling lines; everything else is
  grouped into paragraph-like blocks using a sentence-boundary heuristic. Table regions are
  excluded from that plain-text stream on a per-word basis (not per-character), specifically so
  a table header that spans a ruling line can't get sliced in half.

Whatever the format, the output is the same shape: an ordered list of small, independent
**segments** — a paragraph, a table row, or a single table cell, never the whole document at
once. This granularity is a deliberate middle ground: sentence-level would lose paragraph
context, whole-document-level would mean one bad response forces a retry of the entire document
and blocks any parallel translation. Paragraph/cell-level is small enough to retry cheaply and
translate concurrently, and large enough to still mean something on its own — and it happens to
match exactly what the review screen needs anyway: one editable, one highlightable unit per row.

Table cells also carry extra context forward: their column header and the rest of that row. A
bare cell containing just `"900"` is meaningless in isolation — see the bugs section below for
what happened the one time this wasn't provided.

### Source language detection

Its own small, separate Claude call, not a local statistical detector — two local options were
tested directly first and rejected: neither reliably cleared 90% confidence across all 9 real
test documents, tripped up by short technical fragments and by vocabulary shared between related
languages (e.g. "Korpus" is spelled identically in German and Dutch).

It does not read the whole document, and it does not just read the start. Very short segments
(under 15 characters) are discarded as unhelpful, and the first couple of remaining segments are
skipped too, specifically to avoid a letterhead or title line in one language skewing a document
that's actually written in another. From what's left, an evenly-spaced sample is taken across
the *entire* document — roughly every Nth segment, not the first N — capped at a modest amount
of total text. Research on language identification specifically found that accuracy plateaus
around that size, and more text beyond it tends to add noise rather than help; this was
confirmed by testing, not just assumed, at 9 out of 9 real documents correctly identified. That
whole sample goes into a single call, forced to answer with one of exactly 4 supported
languages plus a confidence level.

### Building the actual translation request

For every segment, for every target language, a request is assembled from four ingredients:

- **Rules** — the same fixed instructions every time: never alter a URL placeholder token; any
  glossary match must use its exact mandated translation, no improvising; a synonym counts as a
  real match too; apply normal sentence casing rather than the glossary's raw stored form;
  anything marked do-not-translate stays byte-for-byte identical; a bare number or code
  translates as just that value, without inventing a sentence around it; and every notable term
  encountered must be classified into one of 5 fixed categories.
- **The glossary**, filtered down to only the entries relevant to this exact source-to-target
  language pair. Synonyms are included too, but only from the synonym column matching the
  document's own source language — a German document is never checked against English synonyms,
  since it could never contain one.
- **Context**, for table cells only — the column header plus the rest of that row.
- **The text itself**, with any URLs already swapped for opaque placeholder tokens so they can
  never be misread, reformatted, or corrupted along the way.

### Structured output, not free text

The AI isn't allowed to just answer with a translated sentence. Using Claude's tool-calling
feature, it's constrained to return two things together: the translation, and a full breakdown
of every notable term it noticed. For each term, it must decide exactly one category:

- **glossary** — matched a glossary entry, used its mandated translation
- **protected** — matched a do-not-translate entry, left unchanged
- **kitchen_technical** — a real kitchen/furniture-industry term it recognizes, not in the
  glossary, confidently handled — explicitly *not* generic IT, business, or HR terminology, even
  if recognized just as confidently
- **ambiguous** — more than one translation is plausible, not fully certain which fits
- **unknown** — anything else it's unsure about, or a confidently-recognized technical term that
  simply falls outside the kitchen/furniture domain

Alongside the category, it also reports the translation actually used, up to 3 alternatives it
considered, and a one-line reason. This is decided in the same call that produces the
translation — not a separate classification pass — and it's the single output that later powers
both the colored highlighting and half of the quality check.

### From category to color on screen

The category the AI assigned travels forward completely unchanged: saved as-is, returned by the
API as-is. In the browser, the translated sentence is scanned for each reported term's exact
wording (longest terms matched first, so a short word can never carve a fragment out of a longer
one it's part of), and each match gets wrapped in a small marker carrying that term's category.
The category name becomes the styling hook directly — there's no separate color-mapping step or
manual assignment anywhere; the color is a pure, fixed lookup from category to color, one rule
per category, matching the 5 colors defined in the original specification. Clicking a highlighted
term opens a panel showing that same term's full data: category, translation used, alternatives
considered, description, and — for glossary matches — the complete glossary entry.

### Retries and glossary enrichment

Before a result is trusted, it's checked: an empty translation, a placeholder-looking answer, or
a malformed term list triggers up to 2 automatic retries before the segment is instead flagged
for a human to review. Separately, for any term the AI classified as a glossary match, the full
glossary entry (all languages, not just the one just used) is attached by a plain lookup against
the glossary already loaded in memory — not a second AI call, so it costs nothing extra and can
never be hallucinated.

### Concurrency, and its one real cost

Every segment, for every target language, is translated at the same time, not one after another
— translating to English and Dutch happens simultaneously, not sequentially. The direct cost of
that speed: since each call is fully isolated, the segment translated 50th has no idea what the
segment translated 200th decided for the exact same word. Because that blind spot is structural,
not accidental, a separate, plain-code audit pass runs afterward, once every result is back,
re-reading the finished translation as a whole specifically to catch what isolated parallel
calls can never catch themselves: the same term rendered differently in different places, a
number that silently changed decimal format partway through the document, a segment that still
reads identical to its own source.

### Human review, correction, and approval

Nothing is exported until a person explicitly approves it. Editing a segment by hand overwrites
the same field the rest of the app already reads from, so there's never a separate "draft"
version to keep in sync. Export itself is actively blocked until approval happens — matching the
original specification's own stated order of review, then approval, then export.

### Observability — knowing what the AI actually did, not just that it ran

Every real call to the AI is automatically captured and sent to Langfuse, without any tracing
code needed at each individual call site — the Anthropic SDK itself is instrumented once, up
front, so every call anywhere in the codebase is captured the same way. On top of that automatic
capture, each call is tagged with the business context that Anthropic's own systems have no way
of knowing: which operation this was, which language pair, whether the glossary was in use,
which segment. That's what makes traces actually searchable afterward, rather than just a flat
list of API calls. It's also built to fail safe — if tracing isn't configured, it quietly does
nothing rather than ever being a reason a translation fails. What it actually shows, per call:
the full prompt sent, the full response, cost, latency, and specifically how much of the prompt
was served from cache versus written fresh — which is what makes the prompt-caching design
something that's verifiable, not just claimed.

## Key decisions, and why

| Decision | What was chosen | Why, specifically |
|---|---|---|
| LLM vs. a dedicated translation API (DeepL) | Claude | Checked DeepL's actual `/translate` API reference directly: it accepts glossary term-pairs and returns translated text — full stop. No field for term classification, no reasoning, no alternatives. The core deliverable (§5: click a term, see category + alternatives + description + glossary entry) isn't something DeepL's API does *worse* — it's not a capability the API exposes at all. A hybrid (DeepL for raw translation quality, an LLM layered on top for classification) is a legitimate future idea, noted but not built. |
| Frontend framework | Vanilla HTML/CSS/JS, no framework | Considered Streamlit (not flexible enough for the interactive term-highlighting and click-to-edit UI) and React (component/state management overhead unjustified at this scope — 6 screens sharing one plain state object). |
| Async translation jobs | FastAPI `BackgroundTasks` | Celery/Redis is real infrastructure for a job queue that needs to survive process restarts and scale across workers — correct for production, unjustified for a single-process MVP demo. |
| Multi-language translation | One call to `translate_document()`, internally concurrent via `ThreadPoolExecutor` | Originally the API layer restricted requests to one language at a time even though the AI core already supported concurrency — fixed to actually expose it, since looping over languages sequentially and calling it "simultaneous" wouldn't have been true. |
| Primary keys | UUID, not auto-increment integers | Researched rather than assumed: standard for REST APIs where IDs are used in public URLs (no sequential-enumeration/guessing risk, no coordination needed across distributed inserts). |
| Database schema | Postgres + SQLAlchemy + Alembic migrations | JSONB columns for `terms` (per-term classification) and `structure` (format-specific parser output) — genuinely variable shape, would be mostly-empty rigid columns otherwise. |
| Prompt caching | `cache_control: ephemeral` on the system prompt, verified via real token accounting | The system prompt (rules + glossary) is identical across every segment call for a given language pair — the per-segment context is deliberately kept *out* of the cached prefix, since interpolating it in defeats caching (the prefix would differ on every call, meaning nothing is ever a cache hit). |
| URL handling | Placeholder substitution (`__URL_0__`) before the AI ever sees a URL | Confirmed real: the model once invented markdown link syntax around a plain URL with no brackets in the source, and corrupted the URL while doing it. Placeholders can't be reformatted, since the model never sees the real text. |
| Manual editing scope | One capability only: overwrite a segment's translation text | The full spec (§6) asks for 5 things (edit text, replace terms everywhere, add to glossary, comments, regenerate via AI). Building all 5 is real, separate engineering surface area (glossary write-paths, comment storage, a second paid AI call per regenerate) — one representative slice was built, the rest named and cut explicitly rather than silently skipped. |
| Observability | Langfuse via OpenTelemetry auto-instrumentation of the Anthropic SDK | Every real Claude call (translation, language detection) is automatically traced with business context (which operation, which language pair, which segment) attached on top. |

## Real bugs found and fixed during development

Found through testing against real documents, not hypothetical edge cases.

| Bug | Root cause | Fix |
|---|---|---|
| A systemic failure (e.g. an invalid target-language name) marked a translation job "done" even though every segment inside it had failed | `translate_document()` catches failures *per segment* on purpose, so one bad segment never kills the whole batch — but that also meant a systemic failure never raised at the job level | Check whether *every* segment in a batch failed; if so, mark the job `failed` with the underlying error, not `done` |
| The AI invented markdown-link formatting around a plain URL and corrupted it while doing so | The model was being asked to faithfully reproduce a real URL as plain text | Placeholder substitution — the model never sees or has to reproduce the actual URL |
| A kitchen-industry-only term category was force-applied to clearly out-of-domain terms (APIs, DSGVO, AI agents) found in an unrelated real PDF | The category's own instructions had no escape hatch for "recognized, but not kitchen-related" | Added an explicit instruction: confidently-recognized non-kitchen technical terms go to `unknown`, not `kitchen_technical` |
| `GET .../quality-check` and `GET .../export` both returned 500 for one real translation | The AI occasionally returned `terms` as a raw string (stray tool-call-like text) instead of an array; one shared function that both endpoints depend on read that field with no type guard | Coerce non-list `terms` to `[]` at the point the AI's raw output is first accepted, *and* defensively at every place stored data is read back — found because the shared function had been missed in an earlier, narrower fix |
| A translation's exported `.docx` file used `tempfile.mktemp()` | That function only returns a filename, it never reserves it — a real (if narrow) race condition, and Python's own docs say not to use it | Switched to `tempfile.NamedTemporaryFile(delete=False)`, the same safe pattern already used for the upload endpoint two functions away |
| Langfuse showed zero traces despite dozens of real translations run through the live app | `flush_tracing()` was only ever called from standalone test scripts — the live FastAPI server never called it, and `uvicorn --reload` restarts the whole process (wiping unflushed spans) on every code edit | Added a flush call to the background translation job's `finally` block — runs after the HTTP response is already sent, costing the user nothing |
| The in-app quality check reported one glossary column header ("Stärke (mm)") as translated "5 different ways" | The contradiction-detector groups by literal term name across the whole document — but the AI was reporting the column header as the "term" for every row's cell, with that row's actual value as the "translation" | Identified and documented as a scaling item, not fixed (see below) |

## Path to production

This is an MVP built to prove the core mechanism — glossary-constrained, self-classifying,
quality-checked translation — actually works, not the final system. Everything below is scoped
out or left rough on purpose, with a specific, known approach for closing it:

| Area | Current state (MVP) | Production approach |
|---|---|---|
| Table preview | Review screen renders every segment as flat text; table position (row/column/header) is already stored, and the exported Word/Excel/PDF files reconstruct real tables correctly | Expose that stored position via the API and group consecutive segments into real `<table>` elements client-side, while keeping per-cell term-highlighting and inline editing working |
| Quality check on tables | The contradiction-detector occasionally flags a table's own column header as "translated differently," since it groups by literal term name across the whole document | Exclude terms whose position marks them as a column header from the cross-document contradiction check |
| Glossary data integrity | A few rows in the source spreadsheet have values in the wrong columns (confirmed by reading the raw cells directly — a data issue, not a parsing bug) | A real glossary management interface would validate this exact shape of error at entry time |
| Glossary maintenance | A spreadsheet, reloaded fresh on every translation; adding a term means editing the file directly | A dedicated database table with a management UI — add/edit/delete, versioning, an approval workflow (spec §7) |
| Manual editing | One capability: overwrite a segment's translation text | Term-replace-everywhere, glossary additions from the review screen, comments, re-running the AI on a single segment (spec §6) |
| Users & permissions | None — single implicit user | Role-based access — Übersetzer / Prüfer / Administrator (spec §13) |
| Project lifecycle | No archive, search, or history across sessions | Status transitions, search/filter, audit trail (spec §1, §11, §12) |
| Job execution | FastAPI `BackgroundTasks` — single process, in-memory, lost on restart | A real job queue (Celery/Redis or similar) that survives restarts and scales across workers |
| Deployment | Local processes on one machine | Central webserver, concurrent multi-user access, responsive UI (spec §16) |
| Repeat content | Every segment translated fresh, every time | Translation memory — cache and reuse previous translations for repeated content (spec Phase 2) |

## Project structure

```
backend/
├── api/main.py              FastAPI app — every endpoint
├── api/schemas.py           Pydantic request/response models
├── db/models.py             SQLAlchemy models (Project, Segment, Translation, TranslatedSegment)
├── db/crud.py                All database reads/writes go through here
├── db/session.py             Postgres connection
├── alembic/                  Schema migrations
├── ai/translator.py          The core: glossary-constrained translation + term classification
├── ai/quality_check.py       Post-translation, non-AI consistency checks
├── ai/language_detector.py   Source-language auto-detection
├── ai/glossary_loader.py     Reads the glossary spreadsheet
├── ai/tracing.py             Langfuse/OpenTelemetry instrumentation
├── parsers/word_parser.py, excel_parser.py, pdf_parser.py   Document -> segments
├── parsers/word_writer.py, excel_writer.py, pdf_writer.py   Segments -> exported document
├── parsers/upload_validator.py   Real validation: signature, structure, actual parseability
└── parsers/document_exporter.py  Picks the right writer, enforces export-format compatibility

frontend/
├── index.html
├── app.js      All 6 screens, state, and API calls
└── style.css   Brand values extracted directly from the real Rotpunkt Küchen website

dataset/        Real, varied test documents (German/English/Dutch/French; Word/Excel/PDF)
                used throughout development, plus the company glossary spreadsheet
```

## Running it yourself

Two terminals, both from a machine with Postgres running and `backend/.env` configured
(`ANTHROPIC_API_KEY`, `DATABASE_URL`, Langfuse keys):

```bash
# Terminal 1 -- backend
cd backend
uv sync                                          # only needed once, or on a new machine
uv run uvicorn api.main:app --reload --port 8000

# Terminal 2 -- frontend
cd frontend
python -m http.server 5500
```

Open `http://localhost:5500`. The interactive API explorer is at `http://localhost:8000/docs`.

## Spec compliance

Mapped directly against the German task document's own workflow diagram (§17):

| Spec step | Status |
|---|---|
| Neues Projekt | Done — auto-created on upload, matching §2's own wording |
| Datei hochladen | Done |
| Sprache auswählen | Done — auto-detected source, manual target selection |
| Übersetzung starten | Done — genuinely concurrent across languages and segments |
| Vorschau erzeugen | Done — side-by-side original/translation |
| Fachbegriffe hervorheben | Done — colors match the spec's own mapping exactly |
| Glossarbegriffe anwenden | Done — the core feature |
| Manuelle Korrektur | Done (minimal slice — see above) |
| Qualitätsprüfung | Done — all 5 checks from §8 implemented |
| Freigabe | Done — export is blocked (409) until this happens |
| Export | Done — Word/Excel/PDF, 3 content modes |
| Projekt wird archiviert | Not built — project lifecycle management, §1-adjacent scope |
