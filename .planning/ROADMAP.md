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
**Goal:** Evaluate extraction quality against public benchmarks and internal datasets.
**Mode:** mvp
**Success Criteria:**
1. System passes tests against DocLayNet, PubLayNet, and PubTables-1M benchmarks.
2. Cross-model confidence calibration is verified.
3. Internal cross-domain evaluation set passes, including deliberate gate-failed test cases.
4. Dependency and license audit is completed.

## Phase 5: Productization
**Goal:** Build the visual workspace UI and prepare the system for distribution.
**Mode:** mvp
**Success Criteria:**
1. Two-panel workspace UI (Page Viewer + JSON Editor) is implemented with synchronized selection.
2. Interactive bounding box editing and element inspection (via drawer) are functional.
3. Unified per-page undo/redo history is working.
4. Edited JSON validates against schema v1.1 and exports successfully (errors block export).
5. Headless CLI batch processing is available.

## Phase 6: Future OCR Extension
**Goal:** (Future) Add OCR support for scanned/image-only PDFs.
**Mode:** mvp
**Success Criteria:**
1. `ocr` and `visual_inference` provenance sources become active.
2. `inferred` confidence type is populated.
3. Text extraction pipeline incorporates OCR without breaking schema v1.1 compatibility.

---
*Last updated: 2026-09-08 after initial roadmap creation*
