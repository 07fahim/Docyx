# Requirements: Docyx

**Defined:** 2026-09-08
**Core Value:** Reconstruct each PDF page as a structured, inspectable representation with unified geometry, provenance, and confidence — enabling downstream consumers to understand not just what was extracted, but how and how reliably.

## v1 Requirements

### 1. Document Ingestion & Gate
- [ ] **INGEST-01**: User can upload born-digital PDF documents via the web interface.
- [ ] **INGEST-02**: System rejects image formats (PNG, JPG, TIFF) at upload time with an explicit "Unsupported format" message.
- [ ] **INGEST-03**: System evaluates each page for a machine-readable text layer (the NO_TEXT_LAYER gate).
- [ ] **INGEST-04**: System handles mixed documents gracefully (returning partial results for failed pages without rejecting the entire document).
- [ ] **INGEST-05**: System reports explicit, actionable page-level status (`ok`, `partial`, `failed`).

### 2. Native PDF Extraction
- [ ] **NATIVE-01**: System extracts native text strings from valid pages using PyMuPDF/pdfplumber.
- [ ] **NATIVE-02**: System extracts accurate geometric bounding boxes for all text elements.
- [ ] **NATIVE-03**: System extracts native typography (font family, size, color, weight, style) where available.
- [ ] **NATIVE-04**: System extracts native reading direction (`ltr`, `rtl`, `ttb`, `unknown`) and language per text element.
- [ ] **NATIVE-05**: System maps all extracted geometry into a canonical coordinate system (top-left origin, reference pixels at 150 DPI).
- [ ] **NATIVE-06**: Native text elements are automatically assigned a confidence value of `1.0` and type `"exact"`.

### 3. Layout, Table, & Visual Detection
- [ ] **DETECT-01**: System renders each page (including gate-failed pages) to an image for visual analysis.
- [ ] **DETECT-02**: System detects and classifies layout regions (Heading, Paragraph, List, etc.) using a swappable layout model.
- [ ] **DETECT-03**: System detects tables and extracts cell grid structure (bounding boxes, rows, columns).
- [ ] **DETECT-04**: System detects visual elements (images, logos, signatures, charts).
- [ ] **DETECT-05**: Detected elements are assigned a probabilistic confidence value (`type: "detected"`).
- [ ] **DETECT-06**: System determines a logical reading order for text elements using geometric or model-based methods (gate-passed pages only).

### 4. Output Schema & Diagnostics
- [ ] **OUTPUT-01**: System emits structured JSON adhering to the canonical schema (v1.1).
- [ ] **OUTPUT-02**: Gate-passed pages output layout, table, and text elements into the primary `elements` array.
- [ ] **OUTPUT-03**: Gate-failed pages output detected layout/visual regions into a separate `diagnostic_elements` array with `diagnostic: true`.
- [ ] **OUTPUT-04**: Every element includes explicit provenance tracking (`native_pdf`, `layout_model`, `table_model`, `geometry_inference`, `manual`).

### 5. Web Workspace UI
- [ ] **UI-01**: Workspace displays a dual-panel layout (Page Viewer on the left, JSON Editor on the right).
- [ ] **UI-02**: User can toggle vector overlays for Text, Layout, Tables, Figures, Reading Order, and Confidence on the Page Viewer.
- [ ] **UI-03**: Selection is synchronized natively between the Page Viewer and the JSON Editor.
- [ ] **UI-04**: User can select, move, resize, or delete bounding boxes in the Page Viewer.
- [ ] **UI-05**: User can inspect and modify element properties (Text, Type, Geometry, Provenance) via the Element Inspector drawer.
- [ ] **UI-06**: UI supports a unified, per-page undo/redo history covering both visual and JSON edits.

### 6. Validation & Export
- [ ] **EXPORT-01**: System validates edited metadata against the v1.1 schema before export.
- [ ] **EXPORT-02**: System blocks export if validation errors exist (warnings are allowed but flagged).
- [ ] **EXPORT-03**: User can export the final corrected metadata as a JSON file.

## v2 Requirements

### Analytics & Processing
- **BATCH-01**: CLI support for headless batch processing of PDF directories.
- **API-01**: REST API endpoints for remote document submission and metadata retrieval.

## Out of Scope

| Feature | Reason |
|---------|--------|
| OCR Support | Deferred to Phase 6 extension. Scanned pages are deliberately failed in v1 to minimize GPU/overhead constraints. |
| Image Formats (JPG/PNG) | Require OCR to process text; incompatible with v1 native-text requirement. |
| Image-Based Typography | `visual_inference` typography is reserved for OCR. Native metadata only in v1. |
| Real-time Collaboration | Not required for MVP value proposition. |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| INGEST-01 | Phase 5 | Pending |
| INGEST-02 | Phase 5 | Pending |
| INGEST-03 | Phase 1 | Pending |
| INGEST-04 | Phase 1 | Pending |
| INGEST-05 | Phase 1 | Pending |
| NATIVE-01 | Phase 2 | Pending |
| NATIVE-02 | Phase 2 | Pending |
| NATIVE-03 | Phase 2 | Pending |
| NATIVE-04 | Phase 2 | Pending |
| NATIVE-05 | Phase 1 | Pending |
| NATIVE-06 | Phase 1 | Pending |
| DETECT-01 | Phase 2 | Pending |
| DETECT-02 | Phase 3 | Pending |
| DETECT-03 | Phase 3 | Pending |
| DETECT-04 | Phase 3 | Pending |
| DETECT-05 | Phase 1 | Pending |
| DETECT-06 | Phase 3 | Pending |
| OUTPUT-01 | Phase 1 | Pending |
| OUTPUT-02 | Phase 2 | Pending |
| OUTPUT-03 | Phase 3 | Pending |
| OUTPUT-04 | Phase 1 | Pending |
| UI-01 | Phase 5 | Pending |
| UI-02 | Phase 5 | Pending |
| UI-03 | Phase 5 | Pending |
| UI-04 | Phase 5 | Pending |
| UI-05 | Phase 5 | Pending |
| UI-06 | Phase 5 | Pending |
| EXPORT-01 | Phase 5 | Pending |
| EXPORT-02 | Phase 5 | Pending |
| EXPORT-03 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 30 total
- Mapped to phases: 30
- Unmapped: 0 ✓

---
*Requirements defined: 2026-09-08*
*Last updated: 2026-09-08 after initial definition*
