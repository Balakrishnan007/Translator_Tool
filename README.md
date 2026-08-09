# Rotpunkt Küchen: Übersetzungstool (Translation Tool)

An AI-powered document translation prototype built as a technical deliverable for an
**AI Solutions Engineer** interview. It translates real kitchen-industry business documents
(Word, Excel, PDF) while enforcing a company-specific glossary, classifying every notable term
it encounters, running an automated quality check, and requiring human approval before export.

---

## Table of contents

1. [What it actually does](#what-it-actually-does)
2. [Spec compliance](#spec-compliance)
3. [Data preparation: no dataset was provided](#data-preparation-no-dataset-was-provided)
4. [Architecture](#architecture)
5. [How a translation works](#how-a-translation-works)
6. [Key decisions, and why](#key-decisions-and-why)
7. [Bugs found and fixed during development](#bugs-found-and-fixed-during-development)
8. [Path to production](#path-to-production)
9. [Project structure](#project-structure)
10. [Running it yourself](#running-it-yourself)

---

## What it actually does

```
Upload (.docx / .xlsx / .pdf)
        │
        ▼
Parse into segments (paragraph / table row / table cell)
        │
        ▼
Detect source language (1 AI call, sampled across the whole document)
        │
        ▼
Select target language(s) (one or several at once)
        │
        ▼
Translate + classify every segment, every language (concurrently)
        │
        ▼
Automatic quality check (plain code, over the finished result)
        │
        ▼
Review: side-by-side view, color-coded terms, click any term for detail
        │
        ▼
Manual correction (optional)
        │
        ▼
Approval (export is locked until this happens)
        │
        ▼
Export (Word / Excel / PDF: translation only, bilingual, or a quality report)
```

## Spec compliance

Mapped against the task document's own workflow diagram (§17):

| Spec step | Status |
|---|---|
| Neues Projekt | Done — auto-created on upload (§2) |
| Datei hochladen | Done |
| Sprache auswählen | Done — auto-detected source, manual target selection |
| Übersetzung starten | Done — concurrent across languages and segments |
| Vorschau erzeugen | Done — side-by-side original/translation |
| Fachbegriffe hervorheben | Done — colors match the spec's own mapping |
| Glossarbegriffe anwenden | Done — the core feature |
| Manuelle Korrektur | Done, one capability of five (see [Key decisions](#key-decisions-and-why)) |
| Qualitätsprüfung | Done — all 5 checks from §8 |
| Freigabe | Done — export returns 409 until this happens |
| Export | Done — Word/Excel/PDF, 3 content modes |
| Projekt wird archiviert | Not built — project lifecycle management, out of MVP scope |

Not built at all: glossary management UI (§7), user roles/permissions (§13), dashboard (§14),
settings (§15), InDesign support, and everything listed under the spec's own "Phase 2" heading.
See [Path to production](#path-to-production) for what each would take.

## Data preparation: no dataset was provided

The task document (§2) specifies which file formats the tool must support: Word, Excel, PDF,
and InDesign. No sample documents came with the brief, so a dataset had to be built before any
translation logic could be tested against something real.

`dataset/` contains:

- 10 documents across the 4 target languages (German, English, Dutch, French) and the 3 formats
  that could realistically be produced (Word, Excel, PDF) — InDesign's `.imdd` was not created,
  since it's Adobe's proprietary save format and requires the actual application to produce
- Substantial rather than minimal files: the German technical spec runs 8 pages with several
  real tables (pricing, hardware, dimensions), enough to actually exercise the parsing,
  table-context, and quality-check logic
- A company glossary spreadsheet (§7): one row per term, a term + synonym column per language,
  plus category and do-not-translate flags
- Content drafted from a written brief (`dataset/dataset_brief_for_claude_chat.md`), generated
  with Claude's assistance

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

**Backend**, layered — a request only flows downward:

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
status/polling → review → approval → export) against the REST API, no framework. `state` is one
in-memory object; every screen is a function that re-renders `#app`.

## How a translation works

**Parsing.** Each format has its own parser, no shared logic underneath: Word and Excel have
real internal structure (paragraphs/tables, rows), read directly; PDF has none, so tables are
detected by ruling lines and everything else is grouped into paragraph-like blocks by a
sentence-boundary heuristic, with table regions excluded on a per-word basis so a header
spanning a ruling line can't get sliced in half. All three converge on the same output shape: an
ordered list of small segments (paragraph, table row, or table cell) — the unit small enough to
translate in parallel and retry individually, and exactly what the review screen needs anyway.
Table cells also carry their column header and the rest of their row forward as context, since a
bare cell containing `"900"` is meaningless on its own.

**Language detection.** Its own Claude call, not a local statistical detector — two local
options (FastText, Lingua) were tested directly and neither reliably cleared 90% confidence
across the 9 real test documents, tripped up by short fragments and vocabulary shared between
related languages (e.g. "Korpus" is spelled identically in German and Dutch). Detection runs on
an evenly-spaced sample across the whole document (avoids a letterhead in the wrong language
skewing the result), capped around 1,500 characters — in line with published research on where
language-ID accuracy plateaus, and verified at 9/9 correct on the real dataset.

**Building the request.** Per segment, per target language: a fixed rule set (glossary matches
use their mandated translation, synonyms count as matches, do-not-translate terms stay
byte-for-byte identical, bare numbers/codes don't get a sentence invented around them), the
glossary filtered to just the relevant source→target pair, table-cell context, and the segment
text with URLs already swapped for placeholder tokens — the model once invented markdown link
syntax around a plain URL and corrupted it while reproducing it, so it now never sees the real
URL text at all.

**Structured output.** Claude's tool-calling forces two things back together: the translation,
and a full term breakdown. Every notable term gets exactly one category — `glossary`,
`protected`, `kitchen_technical`, `ambiguous`, or `unknown` (explicitly *not* generic IT/business
terms even when confidently recognized) — plus the translation used, up to 3 alternatives
considered, and a one-line reason. That's decided in the same call as the translation, and it's
what powers both the highlighting and half the quality check.

**Highlighting.** The category travels forward unchanged from the AI's own answer to the pixel
on screen: no separate color-mapping step, just a direct category → color lookup matching the
spec's 5-color scheme. Clicking a highlighted term opens its full record: category, translation
used, alternatives, description, and the complete glossary entry for glossary matches.

**Retries and enrichment.** An empty, placeholder-like, or malformed result triggers up to 2
retries before the segment is flagged for human review instead. Separately, the full glossary
entry (all 4 languages) is attached to every glossary-matched term by a plain in-memory lookup —
no second AI call, so it's free and can't be hallucinated.

**Concurrency.** Every (segment, language) pair translates at the same time via a thread pool.
The cost: each call is isolated, so segment 50 has no idea what segment 200 decided for the same
word. A separate plain-code pass runs afterward, once every result is back, specifically to
catch what isolated calls can't see themselves — the same term rendered two different ways, a
number that silently changed decimal format partway through, a segment identical to its own
source.

**Review, correction, approval.** Editing a segment overwrites the same field the rest of the
app reads from, so there's no separate draft version to keep in sync. Export is blocked (HTTP
409) until a translation is explicitly approved, matching the spec's own review → approval →
export order.

**Observability.** The Anthropic SDK is instrumented once, so every real call anywhere in the
codebase is captured automatically and tagged with business context (operation, language pair,
glossary on/off, segment) that Anthropic's own systems can't know. Fails safe: if Langfuse isn't
configured, tracing quietly does nothing rather than breaking a translation.

## Key decisions, and why

| Decision | What was chosen | Why, specifically |
|---|---|---|
| LLM vs. a dedicated translation API (DeepL) | Claude | DeepL's `/translate` API returns translated text and accepts glossary term-pairs, but has no field for term classification, reasoning, or alternatives. §5's core deliverable (click a term, see category, alternatives, description, glossary entry) isn't a capability the API exposes at all. A DeepL+LLM hybrid is a valid future option, not built here. |
| Frontend framework | Vanilla HTML/CSS/JS, no framework | Streamlit isn't flexible enough for interactive term-highlighting and click-to-edit UI. React's component/state overhead isn't justified at this scope: 6 screens sharing one plain state object. |
| Async translation jobs | FastAPI `BackgroundTasks` | Celery/Redis is real infrastructure for a job queue that survives process restarts and scales across workers: correct for production, unjustified for a single-process MVP demo. |
| Multi-language translation | One call to `translate_document()`, internally concurrent via `ThreadPoolExecutor` | The AI core already supports concurrency internally; looping over languages sequentially at the API layer would make "simultaneous" translation untrue in practice, so the API exposes true concurrent multi-language calls. |
| Primary keys | UUID, not auto-increment integers | Standard for REST APIs where IDs appear in public URLs: no sequential-enumeration risk, no coordination needed across distributed inserts. |
| Database schema | Postgres + SQLAlchemy + Alembic migrations | JSONB columns for `terms` (per-term classification) and `structure` (format-specific parser output): genuinely variable shape, would be mostly-empty rigid columns otherwise. |
| Prompt caching | `cache_control: ephemeral` on the system prompt, verified via real token accounting | The system prompt (rules + glossary) is identical across every segment call for a given language pair; the per-segment context is deliberately kept *out* of the cached prefix, since interpolating it in defeats caching (the prefix would differ on every call, meaning nothing is ever a cache hit). |
| URL handling | Placeholder substitution (`__URL_0__`) before the AI ever sees a URL | The model once invented markdown link syntax around a plain URL and corrupted it in the process. Placeholders can't be reformatted, since the model never sees the real text. |
| Manual editing scope | One capability only: overwrite a segment's translation text | Spec §6 asks for 5 capabilities (edit text, replace terms everywhere, add to glossary, comments, regenerate via AI), each separate engineering surface area (glossary write-paths, comment storage, a second paid AI call per regenerate). One representative slice was built; the rest are named and scoped out explicitly in the table above, not silently skipped. |
| Observability | Langfuse via OpenTelemetry auto-instrumentation of the Anthropic SDK | Every real Claude call (translation, language detection) is automatically traced with business context (which operation, which language pair, which segment) attached on top. |

## Bugs found and fixed during development

Two representative examples, found by testing against the real dataset documents rather than
hypothetical edge cases:

| Bug | Root cause | Fix |
|---|---|---|
| A systemic failure (e.g. an invalid target-language name) marked a translation job "done" even though every segment inside it had failed | `translate_document()` catches failures per segment on purpose, so one bad segment never kills the whole batch — but that also meant a systemic failure never raised at the job level | Check whether every segment in a batch failed; if so, mark the job `failed` with the underlying error, not `done` |
| The AI invented markdown-link formatting around a plain URL and corrupted it while doing so | The model was being asked to faithfully reproduce a real URL as plain text | Placeholder substitution: the model never sees or has to reproduce the actual URL |

## Path to production

This is an MVP built to prove the core mechanism — glossary-constrained, self-classifying,
quality-checked translation — actually works, not the final system. Two kinds of gaps remain:
product features the spec describes that were deliberately scoped down, and operational maturity
any MVP skips but a real deployment can't.

### Feature scope

| Area | Current state (MVP) | Production approach |
|---|---|---|
| Table preview | Review screen renders every segment as flat text; table position (row/column/header) is already stored, and exported Word/Excel/PDF files reconstruct real tables correctly | Expose the stored position via the API and group consecutive segments into real `<table>` elements client-side, keeping per-cell highlighting and inline editing working |
| Quality check on tables | The contradiction-detector occasionally flags a table's own column header as "translated differently," since it groups by literal term name across the whole document | Exclude terms whose position marks them as a column header from the cross-document contradiction check |
| Glossary data integrity | A few rows in the source spreadsheet have values in the wrong columns (a data issue, confirmed by reading the raw cells, not a parsing bug) | A real glossary management interface would validate this shape of error at entry time |
| Glossary maintenance | A spreadsheet, reloaded fresh on every translation; adding a term means editing the file directly | A dedicated database table with a management UI: add/edit/delete, versioning, an approval workflow (spec §7) |
| Users & permissions | None, single implicit user | Role-based access: Übersetzer / Prüfer / Administrator (spec §13) |
| Project lifecycle | No archive, search, or history across sessions | Status transitions, search/filter, audit trail (spec §1, §11, §12) |
| Repeat content | Every segment translated fresh, every time | Translation memory: cache and reuse previous translations for repeated content (spec Phase 2) |

### Production readiness

Sized for the actual target: one internal team, roughly 10 concurrent users, not a public
consumer product. That target shapes every row below.

| Area | Current state (MVP) | Production approach |
|---|---|---|
| Service architecture | A modular monolith: `api → db → ai → parsers`, one deployable unit | Stays a monolith. Microservices only pay off with independent per-service scaling or multiple teams deploying independently; neither applies here |
| Deployment & scaling | Local processes, started manually on one machine | Docker Compose (API + Postgres containers) on a single server is enough for ~10 concurrent users. Kubernetes becomes justified past 100k+ daily users or 5+ independently-scaling services, neither of which applies here |
| Security & input hardening | `upload_validator.py` checks file signature, structure, and real parseability before anything reaches the AI; no auth on any endpoint, no rate limiting | Auth per user, rate limiting, a hard upload size ceiling enforced before a file reaches the parser, dependency and file-format vulnerability scanning |
| Cost control | Prompt caching already cuts repeated system-prompt cost, but nothing stops one user from re-translating the same document 20 times in a row | Per-user/per-project quotas, translation memory doing double duty as a cost control, cost dashboards built on the same Langfuse data already captured |
| Monitoring & alerting | Langfuse shows what happened on any single call, after the fact, only if someone opens the dashboard | Alerting on job failure rate, latency, and cost thresholds; a health-check endpoint wired into real uptime monitoring |
| CI/CD | Tests run locally, on demand (`pytest`) | A pipeline that runs the same suite automatically on every push, blocks merges on failure, and deploys through a staging environment before production |
| Data handling & compliance | Uploaded documents and their translations live in Postgres indefinitely, no retention policy | A defined retention/deletion policy and data-residency review — relevant here specifically because these are real business documents |

## Project structure

```
backend/
├── api/main.py              FastAPI app, every endpoint
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
# Terminal 1: backend
cd backend
uv sync                                          # only needed once, or on a new machine
uv run uvicorn api.main:app --reload --port 8000

# Terminal 2: frontend
cd frontend
python -m http.server 5500
```

Open `http://localhost:5500`. The interactive API explorer is at `http://localhost:8000/docs`.
