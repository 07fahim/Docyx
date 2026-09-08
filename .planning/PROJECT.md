# Docyx

## What This Is

A language-agnostic, page-by-page document analysis tool that takes PDF pages with a machine-readable text layer and converts them into structured metadata — reconstructing each page as a structured representation of its content, layout, typography, tables, visual elements, and reading order. Not just text extraction; the goal is a unified page representation with explicit provenance and confidence tracking.

## Core Value

Reconstruct each PDF page as a structured, inspectable representation with unified geometry, provenance, and confidence — enabling downstream consumers to understand not just what was extracted, but how and how reliably.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Accept PDFs with machine-readable text layer; cleanly flag pages lacking one as `failed` with `NO_TEXT_LAYER`
- [ ] Normalize all outputs into one documented, versioned coordinate system (top-left origin, 150 DPI reference pixels)
- [ ] Extract text with accurate geometry directly from native PDF (PyMuPDF/pdfplumber)
- [ ] Detect layout regions, tables, and visual elements on rendered page images — on both gate-passed and gate-failed pages
- [ ] Produce defensible reading order for multi-column, RTL, and complex layouts
- [ ] Extract native PDF typography (font family, size, color, weight, style) when metadata exists
- [ ] Expose field-level confidence and provenance distinguishing exact extraction (`1.0`) from detected regions
- [ ] Handle text in any language present in the PDF's text layer
- [ ] Handle diverse born-digital page designs without hard-coding any document type
- [ ] Route gate-failed page results to `diagnostic_elements` (separate from `elements`), with diagnostic path optional in v1
- [ ] Support mixed documents gracefully — partial results returned, never reject whole document
- [ ] Output structured JSON/JSONL per the unified schema (v1.1)
- [ ] Pass public-benchmark and internal evaluation tests (DocLayNet, PubLayNet, PubTables-1M)
- [ ] Remain component-swappable at layout, table, and visual-analysis level
- [ ] Provide two-panel workspace UI: Page Viewer + JSON Editor with synchronized selection
- [ ] Support element inspection (geometry, confidence, provenance, typography) via Inspector drawer
- [ ] Enable user correction of extracted data with provenance tracking (`modified_by_user`, `manual` source)
- [ ] Validate and export corrected JSON with error/warning gating
- [ ] Per-page undo/redo shared across Page Viewer and JSON Editor

### Out of Scope

- OCR / scanned document support — deferred to Phase 6 (future extension), no OCR engine/weights/GPU in v1
- PNG/JPG/TIFF input — inherently no text layer, rejected at upload time
- Image-based typography estimation (`visual_inference`) — reserved in schema, unused in v1
- Training foundation models from scratch — project composes existing open-source components
- Real-time collaboration, team management, enterprise admin — later product features
- Mobile-first editing — desktop-optimized for v1
- Document semantic search — later feature
- Subscription/billing UI — later delivery-model decision

## Context

Two detailed design documents define the full system:

- **Backend plan (v6):** `universal_page_document_metadata_extraction_plan_v6.md` — 28 sections covering architecture, pipeline, schema (v1.1), component strategy, error model, evaluation, and 6-phase roadmap
- **UI/UX plan (v3.1):** `universal_page_metadata_extraction_tool_uiux_plan_v3.md` — 49 sections covering upload, processing, workspace, inspector, JSON editor, validation, export, and responsive design

The UI plan cross-references the backend plan by section number (e.g., "backend §18.1"). Both documents must be read together.

**Key architectural facts:**
- Pipeline: text-layer gate → native extraction (if pass) → render page image (always) → layout/table/visual detection → reading order (if pass) → assemble output
- The v5→v6 correction decoupled layout/table/visual detection from the text-layer gate — these stages operate on the rendered page image regardless of gate outcome
- Schema version 1.1 added `diagnostic_elements` for gate-failed pages
- Provenance sources active in v1: `native_pdf`, `layout_model`, `table_model`, `geometry_inference`, `manual`
- `ocr` and `visual_inference` reserved in schema but unused

**Candidate components:** PyMuPDF/pdfplumber (text), pikepdf (structure), LayoutParser/DocLayNet models (layout), Table Transformer/PP-Structure (tables), OpenCV (image processing)

## Constraints

- **Input format**: PDF only — no image formats in v1
- **Text source**: Native PDF text layer only — no OCR engine, model weights, or GPU for OCR
- **Coordinate system**: Top-left origin, reference pixels at 150 DPI — all geometry normalized to this
- **Schema compatibility**: Minor versions backward-compatible; `ocr`/`visual_inference`/`inferred` fields reserved now for future OCR extension
- **Resource scope**: No training from scratch, no large GPU cluster, no paid cloud APIs — compose existing open-source components
- **Licensing**: Dependency/model/data license audit required before any commercial distribution

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| PDF-only input in v1 | Scanned/image files have no text layer; OCR is the largest source of GPU/licensing overhead | — Pending |
| Text-layer gate decoupled from visual detection (v6) | Layout/table/visual stages operate on rendered images, not text; keeps pipeline correct for future OCR extension | — Pending |
| Schema v1.1 with diagnostic_elements | Gate-failed pages can surface real diagnostic info without polluting the primary elements array | — Pending |
| 150 DPI reference pixels, top-left origin | Canonical coordinate system avoids mixed-coordinate bugs across native PDF points and rendered pixels | — Pending |
| Diagnostic path optional in v1 | Avoids mandating extra compute per gate-failed page; architecture supports it without requiring it | — Pending |
| Two-panel workspace (Page Viewer + JSON Editor) | Inspector as drawer keeps workspace uncluttered; JSON scoped to current page only | — Pending |
| One undo/redo history per page, shared across panels | Prevents competing stacks; only Apply actions are undoable | — Pending |
| Component swappability | Layout/table/visual models replaceable without schema changes; provenance tracks which engine produced each element | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-09-08 after initialization*
