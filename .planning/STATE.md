# Project State

## Active Phase

**Current Phase:** 5 — Productization (workspace UI)
**Status:** In progress. Phases 1-4 complete. The CLI shipped in phase 4 and the
viewer's first slice on 2026-09-20. Phase 6 (OCR) was pulled forward and is
measured, not pending.

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

## Phase 6 (OCR) — seam landed early

Pulled forward on the owner's instruction, because "a scanned page fails the gate"
made the tool untestable against the documents they actually have.

| Roadmap criterion | Status |
|---|---|
| 1. `ocr` provenance becomes active | **Done** — `visual_inference` struck from the criterion and measured out, see below |
| 2. `inferred` confidence is populated | **Done** — by `OCRAnalyzer` and nothing else |
| 3. OCR incorporated without breaking schema compatibility | **Done** — schema v1.3 unchanged, contract test green |

Built as the fourth `detector` seam (`docyx/analysis/ocr.py` + a Tesseract adapter),
not as a pipeline branch: no default recogniser, no new core dependency, and the
default pipeline behaves exactly as before.

**Measured** (`scripts/measure_ocr.py`), against the native text of a page whose
layer was destroyed by rendering it to pixels:

| page | lang | native layer | sequence | char overlap |
|---|---|---|---|---|
| `arxiv_attention` p2 | eng | clean | 0.959 | 0.999 |
| `wiki_bn` p5 | ben | clean | **0.987** | 0.986 |
| `word_bn` p0 | ben | `COMBINING_MARK_ORDER` | 0.705 | 0.921 |
| `wiki_ar` p6 | ara | `RTL_VISUAL_ORDER` | 0.140 | 0.928 |

The two clean rows are the control and they are what make the other two
readable: Tesseract scores 0.96-0.99 where the reference is trustworthy, so the
low sequence scores are the *reference* being wrong, not the recogniser. The
prediction that Bengali accuracy would reverse the engine choice was wrong.

`--ocr-repair` acts on this: on a page warned `RTL_VISUAL_ORDER` or
`COMBINING_MARK_ORDER`, OCR text becomes `elements` and the native text moves to
`diagnostic_elements`. Still unmeasured: any real scan.

## First contact with real documents

13 PDFs from Bangladesh Bank's public circulars, 6 producers, run through the
pipeline without being chosen to prove anything:

- **11 processed cleanly.** No crash, no error. First evidence the pipeline
  survives documents it was not built against.
- **1 hit `COMBINING_MARK_ORDER`** — a Word-produced Bengali policy circular
  whose text layer fails three ways on one page: substituted characters, one
  glyph mapped to a wrong multi-character sequence, and runs lost to
  whitespace. `--ocr-repair` reads it correctly.
- **1 was a genuine scan** (`SECnvtToPDF`), handled as `NO_TEXT_LAYER` and read
  correctly with `--ocr`.

Corrects two working assumptions: Word is not itself the defect (7 of 13 are
Word-produced, 1 damaged), and scans turn up in ordinary document bundles even
when nobody set out to collect them.

## Phase 5 — Workspace (in progress)

**First slice shipped** (`72eb41a`): `python -m docyx.workspace file.pdf` renders
each page with its extracted elements drawn over it, coloured by
`confidence.type`, click a box for its JSON, warnings as a banner.

Built as a feedback loop, not a feature. The phase 1-4 code review found 12
issues, **three of them regressions that 219 passing tests missed** — including
`--layout` taking a page from 3 headings to zero — because no test rendered the
output the way a user sees it.

**Second slice shipped** (`a57f469`, `f096445`): the viewer was rebuilt around
the trust question and the selection loop now closes both ways.

| criterion | state |
|---|---|
| 1. Two-panel UI, synchronized selection | **Done** — reading-order outline drives the page, the page drives the outline |
| 2. Interactive bbox editing + inspection | **Done** — drag to move, eight handles to resize, via `edit_geometry` |
| 3. Per-page undo/redo | **Done** — snapshot restore, not reverse-edit |
| 4. Edited JSON validates and exports, errors block | **Done** — validate then write; schema errors block, a `failed` page does not |
| 5. Headless CLI batch | Done (shipped in phase 4) |

The outline lists the page in reading order, so **the list is the ordering
claim itself** — a wrong order reads as prose that stops making sense, the
same signal `export/markdown.py` is kept around to provide. Top-level
elements only: the first version flattened children and buried 10 lines
under 56 `text_span` fragments, which is the mistake reading order avoids
by refusing to number a table alongside the text inside it.

Design tokens came from Linear's public analysis on getdesign.md — the
ladders only, not the landing-page rhythm. That pass caught a real defect:
focus rings used the `detected` blue, so a focus ring and a detector's
claim were the same colour.

**Rendering it caught four regressions no test could**, which is the
argument for the viewer restated: a font size printed as
`11.039999961853027pt`, a selection scrim opaque enough to make the rest of
the page unreadable, a solid green trust bar on a page whose verdict reads
`PARTIAL`, and light Windows scrollbars on a near-black UI.

**Third slice: text editing.** `POST /api/edit` calls `edit_text()` on the
cached `Document` — the only writer, so the provenance rules hold by
construction rather than by convention. Six tests pin them over HTTP, not
just at the model: source unchanged, `original_text` from the first edit
only, survives re-requesting the page, unknown id is 404 not 500, oversized
body is 413, and a child element is reachable by id.

Only whole lines are editable in the UI. A `text_span` is a fragment of its
line, so correcting one would leave the parent's text stale — the §5
line-granularity rule applied to writes.

**Fourth slice: bbox editing, undo/redo, export — phase 5 criteria 2, 3
and 4.** Schema **v1.7** adds `Provenance.original_geometry`.

Three bugs the work surfaced, each now pinned:

1. **Sharing one guard loses an edit.** Gating both originals on
   `modified_by_user` means whichever edit came second records nothing:
   correcting the text of a box you had already moved silently discarded
   the geometry the machine proposed. The two are guarded separately now,
   and `edit_text`'s own guard moved to `original_text is None`.
2. **`_snapshot`, not reverse-edit.** Undo restores text, geometry,
   confidence and provenance together. "Edit it back" would leave
   `modified_by_user` set and the element `exact`/1.0 — claiming a human
   vouched for a value they took back.
3. **The 413 never reached the client.** Answering an oversized POST
   without draining the body resets the connection, so the caller saw a
   transport error rather than the status. Found by a test that had been
   passing against the *wrong* server, see below.

**A fixture bug hid state leaking between tests.** `base_url` used a fixed
port, so only the first `serve` ever bound it and every later test talked
to the first test's `Workspace`. Invisible while the tests were read-only;
wrong the moment one of them edits. A port per test now.

Remaining, and stated plainly: **edits live in the process.** Export is the
only way out — there is no resume.

**Measured** (`scripts/measure_id_stability.py`), because "ids might not be
stable" was a worry rather than a finding. Ids are positional, so they are
stable against everything the runtime varies — 1092 elements over 4
documents, 0 collisions, 0 moved across repeat runs, across `pages=[n]` vs a
whole-document run, and with layout/table/visual detectors switched on.

`--against REF` answers the question the other four cannot:

```
4b9b461 -> HEAD
  arxiv_attention.pdf   127 -> 80   kept  22  lost 105  new  58  moved  1
  wiki_ar.pdf           178 -> 200  kept 151  lost  27  new  49  moved 28
```

The span-to-line change orphaned **83%** of one page's ids, and 29 ids
survived while naming *different content* — the failure that silently
reattaches a correction to the wrong line. One is traceable to the
"blank spans must not be skipped" fix.

**Verdict: resume is safe within a version, unsafe across one** — so it was
built to verify rather than to trust.

**Fifth slice: resume.** `POST /api/import` → `Workspace.restore()`. The
export is the save file; there is no second format. The check is
`original_text` / `original_geometry` against what the extractor says *now*
— the machine's own claim at the time of the edit. Four outcomes: reattach,
`unchanged`, `EDIT_CONFLICT`, `EDIT_ORPHANED`, and only the first writes.

Verified end to end across a real process restart: edit → export → kill the
server → start a new one → Import. `EDITED 0` became `EDITED 1` with the
corrected Bengali and an undoable history entry. Then the saved
`original_text` was tampered with and re-imported: **0 reattached, 1
conflicted, nothing written**, with the conflict rendered beside the
`COMBINING_MARK_ORDER` warning naming both values.

`unchanged` exists because without it a second import reports every edit as
a conflict — the target no longer matches its own `original_text`, since it
is now the correction.

Edits still do not survive without an explicit Export; there is no
autosave, and that is the next judgement call rather than a defect.

## Decisions settled

- **Licensing** (2026-09-20): keep PyMuPDF, ship AGPL-3.0, open source, buy
  nothing. Safe to defer because containment is enforced by a test. See
  LICENSING.md.
- **Positioning**: the wedge is detecting PDFs whose text layer is damaged while
  the page looks correct — not "more metadata than Adobe".

## Next Steps

1. **Workspace: editing.** The selection loop is closed; `edit_text()` is
   still uncalled. This is what turns the viewer into an annotation tool
   and unblocks (4).
2. **Annotate reading order first** — it is the only thing with a scorer
   (`measure_reading_order.py`). Decide what happens to truth-file checksums
   before element boundaries become editable, or the 0.976 baseline is lost.
3. **Build scorers** for semantic roles and table structure. Annotating them
   without one produces data nothing measures against. This is real work, not
   a side effect of (2).
4. **Expand the corpus** from 8 labelled pages once annotation is cheap.
5. **More real scans.** The first one is measured — `81_Annexure-1.pdf`,
   a Bangladesh Bank return, **CER 0.075** against a hand-typed reference
   (`.corpus/truth/real_81_annexure.p0.json`), or 0.037 excluding the
   form's dotted leaders, with all 44 distinct Bengali glyphs recovered.
   Over half the measured "error" turned out to be leader dots collapsing,
   which changes no meaning. One page, one producer, one transcriber — a
   data point, not a benchmark. The gap now is *breadth*: a second scanner
   and a skewed page, since the sweep says skew is the degradation that
   costs Bengali the most.

## Known gaps, measured

| gap | state |
|---|---|
| Layout model | weakest component. No `Title` on a paper's title page; region grouping scored *worse* (1.000 to 0.519) |
| Reading order | 0.976 tau / 0.957 adj. Multi-line table cells at 0.808 are a semantic ambiguity geometry cannot resolve |
| OCR | one real scan measured, CER 0.075 (0.037 excluding form leaders), 44/44 Bengali glyphs. Everything else is flattened born-digital |
| Corpus | 8 labelled pages, 5 documents, ~20 real files, 2 sources |
| `reading_order` provenance | §10 asks for confidence and provenance; it is a bare int |
| §10 step 1 | "use native PDF structure" — **measured and declined**: heading tags cover 6% of pages, and PyMuPDF exposes no MCID to link one to a line. See CLAUDE.md |
| `schema/v1.6.json` | `description` values edited in place under a published version |

## Project Reference

See: .planning/PROJECT.md

**Core value:** Reconstruct each PDF page as a structured, inspectable representation
with unified geometry, provenance, and confidence — enabling downstream consumers to
understand not just what was extracted, but how and how reliably.

*Last updated: 2026-09-20*
