# Roadmap: Docyx

**Mode:** mvp

## Phase 1: Foundation
**Goal:** Establish the core schema, coordinate system, provenance/confidence model, error model, and decoupled text-layer gate.
**Mode:** mvp
**Success Criteria:**
1. Unified page/element schema (v1.1) defined with `diagnostic_elements` array.
2. Canonical coordinate system (top-left, 150 DPI) and shared geometry object established.
3. Provenance model and confidence representation (`exact`, `detected`) structured.
4. Text-layer presence gate (`NO_TEXT_LAYER`) built and decoupled from layout/vision stages.

## Phase 2: Native PDF Extraction
**Goal:** Extract text, geometry, and fonts from native PDFs and render page images.
**Mode:** mvp
**Success Criteria:**
1. Pages are rendered to images regardless of gate outcome.
2. Native text, bounding boxes, and typography are extracted from gate-passed pages.
3. Extracted geometry is correctly normalized to canonical coordinates.
4. First end-to-end JSON output is produced, including a test case for a gate-failed page.

## Phase 3: Layout, Tables, Reading Order
**Goal:** Detect layout regions, tables, visual elements, and establish reading order.
**Mode:** mvp
**Success Criteria:**
1. Layout region and visual-element detection runs on both gate-passed and gate-failed pages.
2. Table detection identifies bounding boxes, rows, columns, and cells.
3. Reading order is computed successfully for gate-passed pages.
4. Gate-failed pages route layout/visual output to the `diagnostic_elements` array.

## Phase 4: Evaluation and Hardening
**Goal:** Evaluate extraction quality against ground truth the project controls, and
against the tools it would actually be chosen over.
**Mode:** mvp
**Success Criteria:**
1. **Injected detectors are verified through the seam, and their published scores
   cited rather than reproduced.**
2. *(Deferred to Phase 5)* Cross-model confidence calibration.
3. Internal cross-domain evaluation set passes, including deliberate gate-failed test cases.
4. Dependency and license audit is completed.

**Why 1 was rescoped twice.** First, as written it read as a Docyx quality gate and is
not one: DocLayNet and PubLayNet grade *layout detection*, which is a stub here, and
PubTables-1M grades table structure, which is Table Transformer's.

Then the rescoped version was dropped too, because re-running those benchmarks would
reproduce a number the model's authors already published, at a cost of 30-200 GB of
downloads, and would teach nothing about this codebase. The question worth answering is
not "how good is Table Transformer" — Microsoft answered that — but "does a model
injected through our seam arrive intact, attributed to itself".

**Verified** on `arxiv_gpt3.pdf` p7 with the real `TableTransformerDetector`:

| check | result |
|---|---|
| table element produced | yes, 64 cells |
| `provenance.engine` | `microsoft/table-transformer` — the detector, not the analyzer |
| `provenance.source` | `table_model` |
| `confidence.type` | `detected` |
| cell text | from the native layer (`GPT-3 Small`, `125M`) — no OCR |
| `text` elements unaffected | 114, all still `exact` / `native_pdf` |

The seam contract is pinned by `tests/test_table_transformer.py` — engine attribution,
native-layer cell text, and the fallback when a detector declares no engine.

**Why 2 was deferred.** Calibration compares a confidence value against observed
correctness. Native text is `1.0 / exact` by construction and detector confidence is
the detector's, so with layout classification still a stub there is no Docyx-owned
probability to calibrate. Revisit once a real layout model is wired in.

**What criterion 3 turned out to be worth.** The evaluation set is not a formality —
building it found five defects, four now fixed, every one invisible to the English
corpus and three invisible until a second PDF producer existed. See `STATE.md`.

## Phase 5: Productization
**Goal:** Build the visual workspace UI and prepare the system for distribution.
**Status:** In progress. Criterion 5 (CLI) shipped in phase 4. The viewer's
first slice — page, boxes, click for JSON — shipped 2026-09-20; editing and
JSON-to-page selection remain.
**Mode:** mvp
**Success Criteria:**
1. Two-panel workspace UI (Page Viewer + JSON Editor) is implemented with synchronized selection.
2. Interactive bounding box editing and element inspection (via drawer) are functional.
3. Unified per-page undo/redo history is working.
4. Edited JSON validates against schema v1.1 and exports successfully (errors block export).
5. Headless CLI batch processing is available.

## Phase 6: OCR Extension
**Goal:** Add OCR support for scanned/image-only PDFs. **Seam implemented ahead of
phase 5** — see STATE.md. The recogniser adapter is written but unverified; what
remains of this phase is evidence, not code.
**Mode:** mvp
**Success Criteria:**
1. `ocr` and `visual_inference` provenance sources become active.
2. `inferred` confidence type is populated.
3. Text extraction pipeline incorporates OCR without breaking schema v1.1 compatibility.

---
*Last updated: 2026-09-08 after initial roadmap creation*
