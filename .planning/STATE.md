# Project State

## Active Phase

**Current Phase:** 4 — Evaluation and Hardening
**Status:** Complete. Phase 5 (workspace UI) is next; its CLI deliverable shipped early.

Phases 1–3 are implemented and committed; this file was never updated as they
landed and claimed "Current Phase: None" until 2026-09-14.

## Phase 4 progress

| Success criterion | Status |
|---|---|
| 1. Verify injected detectors through the seam; cite published scores | **Done** — real Table Transformer verified end to end on arxiv_gpt3 p7 |
| 2. Cross-model confidence calibration | **Deferred to Phase 5** — nothing Docyx-owned to calibrate while layout is a stub |
| 3. Internal cross-domain evaluation set passes, incl. gate-failed cases | **Done** |
| 4. Dependency and licence audit | **Done** — `LICENSING.md`, including the Table Transformer weights |

### Criteria 1 and 2 — rescoped, see ROADMAP.md

Done. Criterion 1 now benchmarks the injected detector and attributes the score to
it; criterion 2 is deferred to Phase 5, because with layout classification a stub
there is no Docyx-owned probability to calibrate.

### Criterion 3 — what exists

- `.corpus/truth/` — 8 hand-labelled reading-order pages across 5 documents,
  4 genres (two-column academic, monospace spec, wide table, government report)
  and 3 scripts (Latin, Arabic, Bengali).
- `scripts/measure_reading_order.py` — tau + adjacency against truth, reporting the
  naive baseline per page so a non-discriminating page cannot inflate the mean.
- `scripts/compare_tools.py` — Docyx vs Docling on the same pages.
- `scripts/measure_bidi.py` — RTL storage behaviour grouped by PDF `/Producer`.
- Gate-failure coverage: `NO_TEXT_LAYER`, `TEXT_LAYER_SUSPECT`, `RTL_VISUAL_ORDER`,
  `COMBINING_MARK_ORDER`.

Current: **0.976 tau / 0.957 adjacency** over 8 pages, +0.146 tau / +0.329 adj over naive.

### Defects this evaluation found

Five, four fixed, all invisible to the pre-existing English corpus:

1. Vertically-set margin text defeated column detection (cost 0.22 on one page).
2. RTL direction detection was dead on real Arabic — it trusted `span["bidi"]`,
   which real PDFs report as 0.
3. RTL text stored in visual order was returned as `exact` / `confidence 1.0`.
4. Bengali from Word was silently scrambled — glyph order, every character present,
   reported `ok`. Only a second PDF producer could reveal this.
5. Band ordering ignored writing direction, reversing every multi-element RTL row.

Then, after a phase 1-4 code review and an independent audit of the evaluation
itself:

6. Four-column layouts were impossible — the column guard was unsatisfiable above
   three, so such pages fell back to interleaved banding.
7. `source_type` was hardcoded `born_digital`, so it was wrong on every scanned page.
8. One corrupt page raised out of `process()` and lost the whole document.
9. A `.docx` was accepted and reported `ok`, violating the PDF-only scope limit.
10. A bilingual line's RTL run came back reversed and unflagged, because the
    visual-order check only inspected majority-RTL lines.

Not fixed — **floats are the measured ceiling**. A figure caption interleaved with
body text needs layout classification, not threshold tuning. See
`.corpus/truth/wiki_ar.p6.json`.

### Known gaps in the evaluation itself

- One Arabic page; no Bengali reading-order truth.
- Two producers of RTL content (Chrome/Skia, Word 2021) — enough to show the
  finding is not tool-specific, not enough to call it universal.
- Forms are deliberately unlabelled: a tax form's 488 lines have no unambiguous
  linear order.

## Next Steps

1. **Run the CLI on real documents.** Every gap recorded here was found by poking
   at the code; none came from a document someone actually needed to process.
   Nothing else should be built until that produces information.
2. Then, informed by (1): OCR (largest coverage gap), a layout model (fixes the
   two worst scores and is the author's own field), or the phase-5 UI.

## Project Reference

See: .planning/PROJECT.md

**Core value:** Reconstruct each PDF page as a structured, inspectable representation
with unified geometry, provenance, and confidence — enabling downstream consumers to
understand not just what was extracted, but how and how reliably.

*Last updated: 2026-09-14*
