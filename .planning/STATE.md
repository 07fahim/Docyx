# Project State

## Active Phase

**Current Phase:** 4 — Evaluation and Hardening
**Status:** In progress.

Phases 1–3 are implemented and committed; this file was never updated as they
landed and claimed "Current Phase: None" until 2026-09-14.

## Phase 4 progress

| Success criterion | Status |
|---|---|
| 1. Benchmark the *injected detector*, attributed to the detector | Rescoped in ROADMAP.md; not started |
| 2. Cross-model confidence calibration | **Deferred to Phase 5** — nothing Docyx-owned to calibrate while layout is a stub |
| 3. Internal cross-domain evaluation set passes, incl. gate-failed cases | Substantially done |
| 4. Dependency and licence audit | Done (`LICENSING.md`); model-weights audit still outstanding |

### Criteria 1 and 2 — rescoped, see ROADMAP.md

Done. Criterion 1 now benchmarks the injected detector and attributes the score to
it; criterion 2 is deferred to Phase 5, because with layout classification a stub
there is no Docyx-owned probability to calibrate.

### Criterion 3 — what exists

- `.corpus/truth/` — 6 hand-labelled reading-order pages across 3 genres
  (two-column academic, monospace spec, wide table) and 3 scripts (Latin, Arabic).
- `scripts/measure_reading_order.py` — tau + adjacency against truth, reporting the
  naive baseline per page so a non-discriminating page cannot inflate the mean.
- `scripts/compare_tools.py` — Docyx vs Docling on the same pages.
- `scripts/measure_bidi.py` — RTL storage behaviour grouped by PDF `/Producer`.
- Gate-failure coverage: `NO_TEXT_LAYER`, `TEXT_LAYER_SUSPECT`, `RTL_VISUAL_ORDER`,
  `COMBINING_MARK_ORDER`.

Current: **1.000 tau / 0.991 adjacency**, +0.321 over the naive baseline.

### Defects this evaluation found

Five, four fixed, all invisible to the pre-existing English corpus:

1. Vertically-set margin text defeated column detection (cost 0.22 on one page).
2. RTL direction detection was dead on real Arabic — it trusted `span["bidi"]`,
   which real PDFs report as 0.
3. RTL text stored in visual order was returned as `exact` / `confidence 1.0`.
4. Bengali from Word was silently scrambled — glyph order, every character present,
   reported `ok`. Only a second PDF producer could reveal this.
5. Band ordering ignored writing direction, reversing every multi-element RTL row.

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

1. Extend the truth set — Bengali reading order, a second Arabic page, a second
   producer of RTL content that stores *logical* order (never yet tested).
2. Table structure: the markdown export now renders a detected-as-prose table as
   rows, but real structure still needs a table model through the `detector` seam.

## Project Reference

See: .planning/PROJECT.md

**Core value:** Reconstruct each PDF page as a structured, inspectable representation
with unified geometry, provenance, and confidence — enabling downstream consumers to
understand not just what was extracted, but how and how reliably.

*Last updated: 2026-09-14*
