# AGENTS.md

## What this repo is

Planning-only repository — no code yet. Two design documents define a **page-level PDF metadata extraction tool** (project name: Docyx):

| File | Covers |
|---|---|
| `universal_page_document_metadata_extraction_plan_v6.md` | System architecture, pipeline, schema, component strategy, roadmap |
| `universal_page_metadata_extraction_tool_uiux_plan_v3.md` | Website UI/UX — upload, workspace, inspector, JSON editor, export |

The UI plan cross-references the backend plan by section number (e.g., "backend §18.1"). Both documents must be read together; neither is self-contained.

## v1 scope constraints (non-negotiable)

- **PDF-only input** — no PNG/JPG/TIFF. Reject non-PDF at upload time.
- **Requires machine-readable text layer** — no OCR in v1. No OCR engine, no OCR model weights, no GPU for OCR.
- **Text-layer gate** (`NO_TEXT_LAYER`) determines whether a page produces a valid result — but does NOT block layout/table/visual detection from running (v6 decoupling).
- Gate-failed pages: `status: "failed"`, `elements: []`. Any visual analysis output goes to `diagnostic_elements` (separate array, `diagnostic: true`). Running the diagnostic path is optional in v1.
- Mixed documents (some pages pass, some fail) are expected — return partial results, never reject the whole document.
- Provenance sources active in v1: `native_pdf`, `layout_model`, `table_model`, `geometry_inference`, `manual` (user-created). `ocr` and `visual_inference` are reserved but unused.
- Native text confidence is always `1.0` / `type: "exact"`. Probabilistic confidence only applies to detected elements (layout/table).
- Schema version: `1.1` (v6 added `diagnostic_elements`).

## Architecture essentials

- Coordinate system: top-left origin, reference pixels at 150 DPI.
- Geometry shape is unified across all element types: `bbox` (required) + optional `polygon` + optional `rotation`.
- Pipeline order: text-layer gate -> native extraction (if pass) -> render page image (always) -> layout/table/visual detection -> reading order (only if gate passed) -> assemble output.
- Reading order is the only stage genuinely dependent on text. Layout, table, and visual detection operate on the rendered page image.
- Candidate libraries: PyMuPDF/pdfplumber (text), pikepdf (structure), LayoutParser/DocLayNet models (layout), Table Transformer/PP-Structure (tables), OpenCV (image processing).

## Implementation roadmap phases

1. Foundation — schema, coordinate system, provenance/confidence model, error model, text-layer gate (built decoupled from the start)
2. Native PDF extraction — page splitting, rendering, text/geometry/font extraction, first end-to-end JSON output
3. Layout, tables, reading order — layout regions, table structure, visual elements, reading order, diagnostic_elements
4. Evaluation — benchmarks (DocLayNet, PubLayNet, PubTables-1M), confidence calibration, performance profiling
5. Productization — API, batch processing, CLI, optional web interface
6. Future OCR extension (does not block phases 1-5)

## UI implementation notes

- Two-panel workspace: Page Viewer (left) + JSON Editor (right), Inspector as drawer.
- JSON editor scoped to current page only, not whole document.
- One undo/redo history per page, shared across Page Viewer and JSON Editor.
- Dense pages: render overlays as vector layer (SVG/canvas), use spatial indexing for hit-testing.
- Confidence overlay should default to highlighting only layout/table elements (text is always 1.0).
- Export: warnings don't block, errors do block.
