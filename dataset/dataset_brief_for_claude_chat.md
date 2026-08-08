# Dataset generation brief — Rotpunkt Küchen AI Translation Tool prototype

## Context
I'm building a prototype AI-powered translation tool for a kitchen furniture manufacturer (Rotpunkt Küchen, a real German company) as part of an "AI Solutions Engineer" interview task. Before building the tool itself, I need a realistic test dataset: sample documents in multiple languages/formats that the tool will later upload, translate, and run quality checks on.

## What the tool does (for context on why the dataset needs these properties)
A browser-based AI app that translates kitchen-industry business documents (Word/Excel/PDF), respecting a company glossary of technical terms and protected product names. Key features the dataset must be able to demonstrate:
- **Term highlighting**: detects kitchen technical terms, glossary matches, unknown/undefined terms, and protected product names/article numbers (that must never be translated)
- **Quality check**: detects untranslated leftover text, missing glossary terms, inconsistent translation of a repeated term, and formatting problems
- Translation direction is **not fixed** — the tool auto-detects source language and translates into one or more selected target languages (any language to any language)

## Company research (use this vocabulary — it's real, verified from Rotpunkt's own site)
- Product lines: kitchen furniture, dressing rooms, utility rooms, bathroom furniture, cabinet/drawer interiors
- Materials/design terms: real wood, "Bioboard", worktops, handles / handleless design, front finishes, carcass options, kitchen lighting
- Layout types: kitchen row, kitchen island, L-shaped kitchen
- Styles: bright kitchen, dark-toned kitchen, modern handleless kitchen, rustic country-house kitchen
- Export markets (for language choice grounding): Netherlands (#1 market), Germany (home market), France, UK (growing), Belgium, Scandinavia, Austria/Switzerland, Benelux, Iberian Peninsula
- **Important**: do NOT invent specific product names/article numbers and present them as Rotpunkt's real catalog — use realistic but clearly fictional product names (e.g. a made-up line name like "Linea 120") and fabricated article numbers (e.g. format `RP-XXXXX-NNNN`). The general industry/material vocabulary should be authentic; the specific SKUs/product line should be invented.

## Languages and why
- **German** — source/home language (matches the real spec document's original language and the company's home market)
- **English, Dutch, French** — target languages, chosen because they're genuine Rotpunkt export markets (UK expanding, Netherlands is the #1 market, France is a top-tier growing market)
- Since the tool is direction-agnostic (any source → any target), the dataset needs **native-language source files in all four languages**, not just German files — this proves auto-detection and any-direction translation actually work, not just one fixed direction.

## The 10-file dataset plan

| # | Language | Format | Content type |
|---|---|---|---|
| 1 | German | Word (.docx) | Technical product spec sheet (cabinet system) |
| 2 | German | Excel (.xlsx) | Price/parts list with article numbers |
| 3 | German | PDF | Product brochure/catalog excerpt |
| 4 | English | Word (.docx) | Customer/dealer correspondence |
| 5 | English | Excel (.xlsx) | Order/parts list with article numbers |
| 6 | Dutch | Word (.docx) | Technical product spec sheet |
| 7 | Dutch | Excel (.xlsx) | Price/parts list with article numbers |
| 8 | Dutch | PDF | Catalog/brochure page |
| 9 | French | Word (.docx) | Customer correspondence |
| 10 | French | Excel (.xlsx) | Price/parts list with article numbers |

## Size targets
- **Word and PDF files: 8–15 pages each, including at least 2–3 tables** (not just prose — realistic technical documents mix narrative sections with data tables: materials, dimensions, hardware, pricing, etc.)
- **Excel files: 4–8 columns, 100–200 rows** — realistic B2B price/parts-list volume, with plausible fabricated article numbers, product names, materials, dimensions, and prices (not hand-typed one by one — generate with realistic variety/patterns)

## Required content triggers — every single file must include all of these
So that every highlighting category and every quality-check rule has something real to catch when the tool processes it:
1. A **plain kitchen technical term** (e.g. carcass, worktop, hinge, front panel — in that file's language)
2. A **term that would match a company glossary** (reused consistently across the document)
3. **The same glossary term repeated multiple times**, translated consistently each time (this is the anchor for a translation-consistency/contradiction check)
4. **One unknown/undefined technical term** — an invented but plausible-sounding proprietary term not in any standard glossary (e.g. a fictional branded system name)
5. **One protected product name or article number** that must never be translated (appears more than once in the document)
6. **One deliberately untranslated leftover phrase** — realistic, since technical/marketing documents often keep certain foreign-language terms or taglines as-is (e.g. an English phrase left inside a German document)
7. **One formatting irregularity** — e.g. one section heading that's styled inconsistently with the others (plain bold text instead of a proper heading style), so a "document structure recognition" step would miss it

## What I need from Claude chat
Please generate these 10 files (or start with just file #1 to review the approach first, then continue) as actual downloadable .docx/.xlsx/.pdf files, following the plan above — realistic, well-structured, professional business/technical documents in the correct language for each row, each one deliberately containing all 7 required content triggers listed above.

## Reference example — content already drafted for file #1 (German Word technical spec, ~4 pages as a starting draft — needs expanding toward the 8–15 page target)
Product: fictional kitchen system "Linea 120". Sections used: 1. Produktübersicht (overview), 2. Materialien (materials table), 3. Maße und Module (dimensions table), 4. Beschläge und Funktionselemente (hardware table + the unknown term "Delta-Führungssystem" and untranslated phrase "Full Extension"), 5. Konstruktion und Montage (construction/assembly, prose), 6. Artikelnummern und Bestellinformationen (article numbers table, protected SKU "RP-LN120-4471" repeated — **this heading is the deliberate formatting irregularity, styled as plain bold text, not a real heading**), 7. Pflege- und Reinigungshinweise (care instructions), 8. Gewährleistung und Kontakt (warranty/contact). The term "Korpus" (carcass) is the repeated glossary term used consistently throughout. To reach 8–15 pages, this draft needs 2–4 more sections (e.g. color/surface variants table, accessories table, example configurations, planning notes) and expanded prose.
