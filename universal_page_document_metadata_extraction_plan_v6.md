# Universal Page-Level Document Metadata Extraction Tool — Project Plan (v6)

**What changed in v6:** one architectural correction to v5 — the text-layer gate (§18) no longer implies that layout, table, and visual-element detection require native text to run. They don't; they operate on the rendered page image (§2.2) regardless of whether text extraction succeeded. A page that fails the gate still cannot produce a valid v1 *result* (that remains a firm product decision), but the pipeline is no longer architecturally wrong about what's technically possible on that page. This also removes a piece of rework the future OCR extension (§20) would otherwise have needed, since OCR fundamentally depends on layout/visual detection running independently of text. Everything else in v5 is otherwise unchanged and considered stable.

---

## 1. Vision

Build a **language-agnostic, page-by-page document analysis tool** that takes PDF pages with a machine-readable text layer and converts them into structured metadata.

The tool should work across different document designs, including:

- Invoices, receipts (digitally generated)
- Digitally exported reports, articles, and research papers
- Contracts and forms filled/generated digitally
- E-books and digitally typeset documents
- Government/administrative documents exported as PDF
- Certificates and resumes exported as PDF
- Brochures, posters, and mixed text/image pages, when exported with a text layer
- Table-heavy pages

**Note on scope:** several of these document types (receipts, newspapers, certificates) are frequently encountered in practice as *scans or photos*, not born-digital PDFs. This plan's v1 is scoped to the digitally-generated subset of each type. Scanned or photographed instances of these documents cannot produce a valid v1 *result* until the OCR extension (§20) is built — though as of v6, the pipeline is no longer architecturally prevented from running visual analysis on such pages; see §18.

The core objective is not simply text extraction. The objective is to **reconstruct each page as a structured representation of its content, layout, typography, and visual elements**.

---

## 2. Core Objective and MVP Input Contract

### 2.1 Input contract

> **The MVP accepts PDFs that contain a machine-readable text layer. Scanned or image-only PDFs cannot produce a valid v1 result.**

This is a product contract, not a technical ceiling — see §18 for the distinction. It means the MVP does not require:

- An OCR engine or OCR model weights
- OCR inference or a GPU for OCR
- OCR confidence handling or calibration
- OCR language configuration or OCR-specific preprocessing

Native PDF extraction is the sole and authoritative text source for v1 results.

### 2.2 A clarifying distinction: no OCR ≠ no image processing

"No OCR" specifically means the system does not perform *text recognition from pixels*. It does **not** mean the system avoids rendering pages to images, and it does **not** mean layout/table/visual detection are unable to run without native text. Layout detection, table detection, and visual-element detection (figures, logos, stamps, signatures) commonly operate on a **rendered image of the page**, independent of whether the page has any text at all — because these are visual/spatial recognition problems, not text-recognition problems. This is architecturally significant, not just a component-selection note: it means these stages have no hard dependency on the text-layer gate (§18) passing. §18 details exactly which stages are and aren't text-dependent.

### 2.3 What the system combines

1. **Native PDF extraction** from the machine-readable text layer and embedded PDF objects, when present.
2. **Layout and visual analysis** (operating on a rendered page image, §2.2), independent of whether native text extraction succeeded.
3. **A unified output schema**, used consistently for every input, including pages that fail the text-layer gate (§18.4).

The system should clearly distinguish between information that is:

- **Extracted directly** (native PDF text, native font metadata)
- **Detected** (a layout model finding a table region, a table model finding cell boundaries)
- **Estimated / inferred** (rare in the core pipeline; reserved mainly for the future OCR extension)
- **Diagnostic, not a valid v1 result** (§18.4) — new in v6, for gate-failed pages

---

## 3. System Architecture

```text
                    INPUT: PDF with machine-readable text layer
                               │
                               ▼
                     Text-layer presence/quality gate (§18.1)
                               │
                   ┌───────────┴───────────┐
                   ▼ (pass)                 ▼ (fail)
          Native PDF Extraction        status: "failed"
          (text, geometry, fonts)      error: NO_TEXT_LAYER
                   │                          │
                   ▼                          │  optional diagnostic
        Render page for layout/visual         │  path (§18.4) — not
        analysis (§2.2) ◄──────────────────────┘  required in v1
                   │
     ┌─────────────┼─────────────┐
     ▼              ▼              ▼
Layout Analysis  Table Analysis  Visual Analysis
     │              │              │
     └──────────────┼──────────────┘
                     │
        ┌────────────┴────────────┐
        ▼ (gate passed)            ▼ (gate failed)
  Reading Order + Tables      diagnostic_elements
  → elements[] (§15)          (kept out of elements[],
        │                      status stays "failed")
        ▼
  Confidence + Provenance
        │
        ▼
  Structured JSON / JSONL

Future, separate path (§20):
Scanned/image-only PDF → OCR adapter → same unified page model
```

**Key change from v5:** rendering and layout/table/visual analysis sit *after* the gate branches, not only on the "pass" side of it. A gate failure changes where the results land (`diagnostic_elements` instead of `elements`, and `status` stays `"failed"`) — it does not prevent those stages from running at all. The architecture remains independent of any specific document domain — an invoice and a digitally-typeset report go through the same pipeline.

---

## 4. Page-Level Metadata

```json
{
  "page_number": 1,
  "width": 1700,
  "height": 2200,
  "coordinate_system": {
    "origin": "top_left",
    "units": "reference_pixels",
    "reference_resolution": 150
  },
  "source_type": "born_digital",
  "status": "ok"
}
```

Fields: page number, width, height, coordinate system (§16), source type (always `"born_digital"` in v1 — the field exists now so the schema doesn't change shape when the OCR extension adds `"scanned"`), and status/errors/warnings (§18).

Orientation and skew are **not core extraction targets**. They may be used internally as preprocessing operations when they improve extraction quality, but are not reported as page metadata.

---

## 5. Text Metadata

```json
{
  "text": "Example text",
  "geometry": {
    "bbox": [120, 240, 680, 85],
    "polygon": null,
    "rotation": null
  },
  "reading_order": 3,
  "language": "en",
  "direction": "ltr",
  "confidence": {
    "value": 1.0,
    "type": "exact"
  },
  "provenance": {
    "source": "native_pdf"
  }
}
```

Confidence for native text is `1.0` / `type: "exact"` — it isn't a model guess, it's a direct read of the PDF's content stream. Confidence becomes meaningfully probabilistic only for *detected* elements (layout regions, table structure) — see §15. This object only exists on pages that pass the text-layer gate; a gate-failed page has no text elements at all (§18.4).

Extraction granularity (character / word / line / text block) should be selected based on what PyMuPDF/pdfplumber expose and downstream requirements — line or block granularity is the recommended default for v1.

---

## 6. Unified Geometry

Every element with spatial extent — text, layout region, table, cell, visual element — uses the same geometry shape:

```json
{
  "bbox": [x, y, width, height],
  "polygon": [[x1, y1], [x2, y2], [x3, y3], [x4, y4]],
  "rotation": 0.0
}
```

- `bbox` is required for every element.
- `polygon` is optional but preferred when the source provides non-axis-aligned geometry — relevant for born-digital PDFs too (rotated stamps, angled vector labels), not just scans.
- `rotation` is optional and should not be fabricated when polygon geometry already encodes orientation.
- All returned geometry is in the canonical page coordinate system (§16); native PDF point coordinates remain available via provenance.

---

## 7. Typography Metadata

Where typography is available in the PDF's native representation:

```json
{
  "font_family": { "value": "Arial", "source": "native_pdf" },
  "font_size": { "value": 12, "unit": "pt", "source": "native_pdf" }
}
```

In v1, typography is **always** `source: "native_pdf"` or absent — there is no image-based typography estimation in the core pipeline; that only applies to raster/scanned content and belongs to the OCR extension (§20), which introduces `visual_inference` typography with an explicit confidence value.

---

## 8. Layout Metadata

Meaningful page regions, each with a `type` and `geometry`: Title, Heading, Paragraph, List, Table, Image, Figure, Caption, Logo, Header, Footer, Form field, Signature, Stamp, Chart, Equation, Other.

```json
{
  "type": "table",
  "geometry": { "bbox": [120, 1000, 2200, 800], "polygon": null, "rotation": null },
  "confidence": { "value": 0.93, "type": "detected" },
  "provenance": { "source": "layout_model", "engine": "...", "version": "..." }
}
```

**Note (v6):** layout detection runs on the rendered page image regardless of text-layer gate outcome (§18.1). On a page that passes the gate, layout regions land in `elements` alongside text. On a page that fails the gate, any layout regions detected land in `diagnostic_elements` instead (§18.4) — same shape, different array.

The layout taxonomy should remain extensible so new document structures can be supported without redesigning the system.

---

## 9. Visual Elements

Non-text objects: images, logos, charts, signatures, stamps, drawings, other visual regions.

```json
{
  "type": "image",
  "geometry": { "bbox": [1500, 300, 700, 500], "polygon": null, "rotation": null },
  "confidence": { "value": 0.97, "type": "detected" },
  "provenance": { "source": "layout_model" }
}
```

Visual-element detection is the stage least dependent on text (§18.1) — it never needed text elements to find a photo or a logo.

---

## 10. Reading Order

Reading order should be represented independently from raw bounding-box position, using a layered strategy:

1. Use reliable native PDF structure when available.
2. Apply geometric ordering methods such as XY-cut or graph-based ordering as the baseline where native structure is absent or unreliable.
3. Evaluate model-based reading-order methods only where they provide a measurable improvement.
4. Preserve the final ordering together with a confidence value and provenance.

Reading order is the one stage that is **genuinely and inherently dependent on text** (§18.1) — ordering has little meaning applied to a page with no text elements, so it is not computed on gate-failed pages even when diagnostic layout/visual results exist for them.

```text
Newspaper:  Headline → Column 1 → Column 2 → Caption
Article:    Title → Author → Paragraph 1 → Paragraph 2
Invoice:    Company info → Customer info → Invoice table → Total
```

---

## 11. Table Structure Extraction

Table *detection* alone is not sufficient. For each detected table, represent: table bounding box, rows, columns, cells with their own geometry, cell text, row/column indices, merged cells where detectable, header/body classification where detectable, and cell-level confidence and provenance.

**Note (v6):** table *detection* and *grid structure* (bounding box, rows/columns from ruling lines or whitespace) can be recovered from the rendered page image without any text present. *Cell text*, obviously, cannot — a table detected on a gate-failed page has geometry but empty cell text. This partial capability is exactly why table results on such pages are diagnostic (§18.4) rather than a valid result: the structure may be real, but the table is not usably populated.

Table structure recognition is a distinct problem from table detection and should be evaluated separately (§21).

---

## 12. Native PDF Extraction Pipeline **[v6 — revised to decouple stages]**

For every page:

1. **Text-layer presence/quality gate (§18.1).** Determines whether native text extraction can proceed to produce a valid result — it does not determine whether anything else in this list can run.
2. **If the gate passes:** extract native text (§5), extract text geometry (§6) and convert to canonical coordinates (§16), extract font/color/style (§7), extract embedded images/objects.
3. **Render the page to an image** for the layout/table/visual stage (§2.2) — this happens **regardless of the gate's outcome**, since these stages don't require native text.
4. Run layout, table, and visual-element detection on the rendered image.
5. **If the gate passed:** compute reading order (§10), and assemble the page's text, layout, table, and visual results into the primary `elements` array (§15).
   **If the gate failed:** layout/table/visual results (if computed — this is optional, §18.4) are attached as `diagnostic_elements`, not `elements`; reading order is not computed; `status` remains `"failed"`.
6. Assemble the unified page schema (§15) with provenance and confidence populated throughout.

The distinction between steps 2 and 3–4 is the core fix in v6: step 2 is gated, steps 3–4 are not.

---

## 13. Language and Direction Support

The architecture is language-agnostic: the system supports any language present in the PDF's text layer and covered by the layout/table/vision components in use.

Writing direction must be tracked explicitly, since not every page has a single global reading direction:

- `direction`: `ltr`, `rtl`, `ttb`, or `unknown`, per text element
- Mixed-direction pages should not be forced into one page-level direction
- RTL/bidirectional reading order is an explicit evaluation category (§21)

Full script-specific glyph shaping is outside the page-metadata scope unless a downstream use case requires it.

---

## 14. Document-Type Independence

```text
                  Universal Page Analyzer
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
     Layout             Tables              Visual
        │                  │                  │
        └──────────────────┼──────────────────┘
                           ▼
                    Page Representation
```

The same representation should describe an invoice, a digitally-typeset article, a report, or a contract equally well. Document-specific interpretation can be added later as a higher-level layer.

---

## 15. Unified Output Schema (Authoritative) **[v6 — `diagnostic_elements` added]**

```json
{
  "schema_version": "1.1",
  "document": {
    "id": "...",
    "source_type": "pdf",
    "page_count": 1
  },
  "pages": [
    {
      "page_number": 1,
      "status": "ok",
      "errors": [],
      "warnings": [],

      "width": 1700,
      "height": 2200,
      "coordinate_system": {
        "origin": "top_left",
        "units": "reference_pixels",
        "reference_resolution": 150
      },
      "source_type": "born_digital",

      "elements": [
        {
          "id": "e1",
          "type": "heading",
          "geometry": { "bbox": [120, 180, 1100, 180], "polygon": null, "rotation": null },
          "text": "Annual Financial Report",
          "reading_order": 1,
          "direction": "ltr",
          "language": "en",
          "confidence": { "value": 1.0, "type": "exact" },
          "provenance": { "source": "native_pdf", "engine": "pymupdf", "version": "..." },
          "typography": {
            "font_family": { "value": "Arial", "source": "native_pdf" },
            "font_size": { "value": 28, "unit": "pt", "source": "native_pdf" },
            "font_color": { "value": "#000000", "source": "native_pdf" },
            "font_weight": "bold",
            "font_style": "normal"
          }
        },
        {
          "id": "e2",
          "type": "table",
          "geometry": { "bbox": [120, 1000, 2200, 800], "polygon": null, "rotation": null },
          "reading_order": 3,
          "confidence": { "value": 0.91, "type": "detected" },
          "provenance": { "source": "table_model", "engine": "...", "version": "..." },
          "table": { "rows": 4, "columns": 3, "cells": [] }
        },
        {
          "id": "e3",
          "type": "image",
          "geometry": { "bbox": [1500, 300, 700, 500], "polygon": null, "rotation": null },
          "confidence": { "value": 0.97, "type": "detected" },
          "provenance": { "source": "layout_model" }
        }
      ],

      "diagnostic_elements": []
    }
  ]
}
```

**`diagnostic_elements` (new in v6):** populated only on pages where `status: "failed"` due to `NO_TEXT_LAYER`, and only if the implementation chose to run the optional diagnostic path (§18.4). Uses the same element shape as `elements`, with `diagnostic: true` set on every entry. `elements` is always empty on a `failed` page — diagnostic content never lands there, so any downstream consumer that reads `elements` without checking `status` first still gets a correctly empty result rather than a misleading partial one.

This is a conceptual contract, not a requirement that every field be populated for every element.

---

## 16. Provenance and Confidence

### 16.1 Canonical provenance sources (v1)

```text
native_pdf         — text, fonts, colors extracted directly from the PDF
layout_model       — region/element detection from a layout model
table_model        — table structure detection
geometry_inference — geometry derived/converted rather than directly read
```

These are the only provenance sources active in v1, used identically whether an element lands in `elements` or `diagnostic_elements` (§15) — the source of a detection doesn't change based on which array it ends up in. `ocr` and `visual_inference` are reserved for the future OCR extension (§20) and remain unused in v1.

### 16.2 Confidence representation

```json
{ "value": 0.91, "type": "exact | detected | inferred" }
```

- `exact` — native PDF extraction; value is always `1.0`.
- `detected` — output of a layout/table/vision model.
- `inferred` — reserved for the OCR extension's visual typography estimation; unused in v1.

### 16.3 Cross-model calibration

Different layout/table models may produce confidence scores with different meanings and scales. Preserve the raw score and its producing engine, and only report a "normalized" score when a documented calibration method exists:

```json
{
  "confidence": { "value": 0.91, "type": "detected" },
  "provenance": { "source": "layout_model", "engine": "example-layout-model", "version": "...", "raw_confidence": 0.87 }
}
```

Never imply cross-model comparability merely by presenting every raw score on a `[0,1]` scale.

---

## 17. Open-Source Component Strategy

| Capability | Candidate components | Role |
|---|---|---|
| Native PDF extraction | PyMuPDF, pdfplumber | Text, spans, fonts, sizes, colors, positions, blocks |
| PDF structure/inspection | pikepdf | Low-level PDF handling |
| Page rendering (for layout/vision — not OCR) | PyMuPDF, pdf2image | Rasterize a page for visual detection models — runs regardless of gate outcome (§12) |
| Layout detection | LayoutParser, DocLayNet-compatible models | Region/block detection on the rendered page |
| Table extraction | Table Transformer (TATR), PP-Structure | Table detection and row/column/cell structure |
| Image processing | OpenCV | Crop, resize, normalize |

No OCR engine appears here — that list exists only in §20.

---

## 18. Page Processing Status and Error Model **[v6 — §18.1 corrected, §18.4 added]**

```json
{ "status": "ok", "errors": [], "warnings": [] }
```

- `ok` — expected extraction completed
- `partial` — some stages succeeded while others failed or produced unusable results
- `failed` — the page could not produce a valid v1 result

### 18.1 The text-layer gate

```json
{ "code": "NO_TEXT_LAYER", "stage": "text_layer_detection", "message": "Page contains no extractable native text" }
```

A page that fails this gate gets `status: "failed"` with this error. **As of v6, this is corrected to mean the gate blocks the page from producing a valid v1 result — it does not architecturally block layout, table, or visual-element detection from running.** Those stages operate on the rendered page image (§2.2) and have no technical dependency on native text.

The dependency on text is not uniform across stages:

- **Visual-element detection** (images, logos, charts, stamps) is fully independent of text.
- **Layout region detection** can still find and classify regions on a text-less page, though thinner without text content to associate with the region.
- **Table detection** can still recover a table's bounding box and grid structure from ruling lines or whitespace, independent of text — it just cannot populate cell text.
- **Reading order** is the one stage genuinely dependent on text (§10) — it is not computed on gate-failed pages.

This correction matters for two reasons: it keeps the pipeline correctly decoupled so the OCR extension (§20) — which fundamentally works by running layout/visual detection first and associating OCR'd text with it afterward — doesn't require redesigning these stages later; and it means a gate-failed page can still surface real diagnostic information instead of nothing (§18.4).

**Other error categories:** invalid/corrupted input, encrypted/unsupported PDF, rendering failure, layout detection failure, table extraction failure, reading-order failure, resource exhaustion, timeout. A failure in one optional capability must not invalidate successful results from another — a page can have valid text and layout output while table extraction fails.

### 18.2 Mixed documents degrade gracefully, by design

Because the error model is per page, not per document, a mixed PDF — a digitally generated cover letter with a scanned exhibit page appended — is handled naturally: pages with a text layer succeed normally, pages without one fail cleanly with `NO_TEXT_LAYER`, and the document as a whole is never rejected outright. This is intentional product behavior: partial results on mixed documents are useful and should be returned, not suppressed.

### 18.3 Text-layer presence is not the same as text-layer quality

Some PDFs — particularly ones from older or non-standard export tools with subsetted fonts and no `ToUnicode` mapping — return text that is technically "extractable" but garbled or incorrect. The gate in §18.1 only checks *presence*, not *correctness*. Test against a deliberately messy set of real-world PDFs (§21), and consider a warning signal for pages that pass the presence check but likely have degraded text quality.

### 18.4 Diagnostic output for gate-failed pages **[v6, new]**

When a page fails the text-layer gate, the pipeline may still run layout, table, and visual-element detection on the rendered page (§18.1). This output is **not** a valid v1 extraction result — the product's input contract requires a usable text layer for a normal result (§2.1) — so it is kept structurally separate from the primary `elements` array (§15):

```json
{
  "page_number": 6,
  "status": "failed",
  "errors": [
    { "code": "NO_TEXT_LAYER", "stage": "text_layer_detection", "message": "Page contains no extractable native text" }
  ],
  "elements": [],
  "diagnostic_elements": [
    {
      "id": "d1",
      "type": "image",
      "geometry": { "bbox": [200, 300, 900, 700], "polygon": null, "rotation": null },
      "confidence": { "value": 0.95, "type": "detected" },
      "provenance": { "source": "layout_model" },
      "diagnostic": true
    }
  ]
}
```

Rules:

- `elements` remains empty for a `failed` page — it never contains diagnostic-only content, so a consumer that reads `elements` without checking `status` first gets a correctly empty result, not a misleadingly partial one.
- `diagnostic_elements` uses the same element shape as `elements`, with `diagnostic: true` on every entry.
- **Running the diagnostic path is optional in v1.** It costs an extra rendering + layout/vision inference pass on a page already known to be out of contract. Implementations may skip it entirely and return `status: "failed"` with an empty `elements` array and no `diagnostic_elements` at all. The architecture must *support* producing diagnostic output; the MVP is not required to always compute it. This keeps the resource-lightness goal of §24 intact — v6 fixes what's architecturally possible, it doesn't mandate more compute per page.

---

## 19. Schema Versioning

```json
{ "schema_version": "1.1", "document": { "...": "..." } }
```

v6 bumps the minor version to `1.1` because `diagnostic_elements` is a new field on the page object — additive, not breaking, consistent with the compatibility policy: major versions may break structure, minor versions stay backward compatible. Component versions (which PyMuPDF version, which layout model version) remain separate in each element's `provenance.engine`/`version`.

---

## 20. Future Extension: OCR and Scanned/Image-Only PDFs

This section is intentionally isolated — nothing in it is required to ship v1, and nothing in §1–§19 depends on it existing. **v6 makes this extension meaningfully easier to build**, because layout/table/visual detection are already decoupled from the text-layer gate (§18.1) — OCR can slot in as exactly the stage that populates `elements` for a page that would otherwise only produce `diagnostic_elements`.

### 20.1 When this gets built

```text
Scanned/image-only PDF
        ↓
Optional preprocessing (deskew, denoise, orientation correction)
        ↓
Layout / table / visual detection on the rendered page (already built in v1, §18.1)
        ↓
OCR (new) — text + text bounding boxes + OCR confidence
        ↓
Associate OCR'd text with the regions already detected
        ↓
Unified page representation (§15) — this page now produces `elements`, not `diagnostic_elements`
```

### 20.2 What activates in the schema

- `provenance.source` gains active use of `ocr` and `visual_inference` (reserved, unused, in §16.1).
- `confidence.type` gains active use of `inferred` (reserved in §16.2).
- `page.source_type` gains the value `"scanned"`.
- Typography (§7) gains image-based estimation, explicitly labeled `source: "visual_inference"` with a confidence value.
- The coordinate system gains active use of `source_resolution`, `source_width`, `source_height`, and `render_scale` for mapping OCR/raster pixel coordinates into the canonical space.
- The `NO_TEXT_LAYER` gate (§18.1) itself doesn't disappear — a scanned page still has no *native* text layer — but once OCR is available, that gate outcome routes to the OCR path instead of to `diagnostic_elements`, and the page can produce a full `elements` result.

None of these require a schema version bump beyond what v6 already introduced, since the fields are reserved now.

### 20.3 Candidate OCR components

| Capability | Candidates |
|---|---|
| OCR text + boxes | Surya, Qari-OCR, Tesseract, PaddleOCR |

### 20.4 Additional evaluation when this ships

CER/WER, OCR confidence calibration (§16.3's approach, applied to OCR), script/language-specific recognition error analysis, image-based typography estimation accuracy — evaluated separately from native PDF typography accuracy.

### 20.5 What does not change

The layout, table, and visual-detection components built for v1 are reused as-is — v6 already made them independent of the text source. Only the text-population stage changes, and where its output lands (`elements` instead of `diagnostic_elements`).

---

## 21. Evaluation Strategy

### 21.1 Benchmark datasets

Public layout/table benchmarks remain valid for v1 evaluation even without OCR, because layout and table detection operate on the rendered page image, not on OCR'd text (§2.2):

| Capability | Candidate datasets |
|---|---|
| General document layout | DocLayNet, PubLayNet |
| Tables | PubTables-1M |
| Forms/receipts | FUNSD, CORD, SROIE — useful mainly once the OCR extension (§20) ships |
| Custom arbitrary page designs | Internal cross-domain evaluation set of born-digital PDFs |

The internal set should specifically include messy, real-world, non-uniform PDFs (§18.3), and — new consideration for v6 — a handful of gate-failed pages (scanned pages fed in deliberately) to verify the diagnostic path (§18.4) behaves correctly: `status: "failed"`, empty `elements`, and (if the implementation runs it) sane `diagnostic_elements`.

### 21.2 Evaluation dimensions

- Native text extraction accuracy
- Text-layer presence/quality detection accuracy (§18.3)
- Text/line bounding-box accuracy
- Layout detection accuracy — measured both on gate-passed and gate-failed pages, since v6 makes the latter a legitimate case to test (§18.4)
- Reading-order accuracy
- Table structure accuracy
- Native typography extraction accuracy
- Field-level confidence calibration (§16.3)
- Processing speed, memory usage

CER/WER and other OCR-specific metrics are **not** part of v1 evaluation — they belong to §20.4.

---

## 22. Preprocessing

Preprocessing exists to improve extraction quality, not to define the product. Potential operations: page rendering resolution choice, image normalization for the layout/vision stage, cropping. Each should be evaluated experimentally; preprocessing configurations should be reproducible.

---

## 23. Development Roadmap (Single, Authoritative)

### Phase 1 — Foundation

- `schema_version` and the unified page/element schema, including `diagnostic_elements` (§15)
- Canonical coordinate system and shared geometry object (§6)
- Provenance model and confidence representation (§16), with `ocr`/`visual_inference`/`inferred` reserved but unused
- Page status/error model, including the text-layer gate (§18.1) **built decoupled from layout/table/visual stages from the start** — this is cheaper to build correctly now than to retrofit later
- Language/direction fields (§13)

### Phase 2 — Native PDF Extraction

- Page splitting and rendering (§2.2, runs regardless of gate outcome)
- Native text extraction, geometry, coordinate normalization (gated, §18.1)
- Native font, color, and style extraction
- Embedded image/object extraction
- First structured JSON output end-to-end, including a test case for a gate-failed page

### Phase 3 — Layout, Tables, and Reading Order

- Layout region detection (§8), run on both gate-passed and gate-failed pages
- Table detection and structure/cells (§11)
- Reading order (§10) — gated on text presence, per §18.1
- Visual-element detection (§9)
- `diagnostic_elements` population logic for gate-failed pages (§18.4), as an optional path

### Phase 4 — Evaluation and Hardening

- Public benchmark evaluation (§21.1)
- Internal cross-domain evaluation set, including messy and gate-failed test pages (§18.3, §21.1)
- Confidence calibration across swappable layout/table models (§16.3)
- Performance profiling and failure-case analysis
- Dependency/license audit (§27)

### Phase 5 — Productization

- Stable API and versioned schema documentation
- Batch processing
- Local/self-hosted packaging (Python library + CLI)
- Optional web interface
- Documentation

### Phase 6 — Future Extension: OCR (§20)

Only if scope expands to scanned/image-only PDFs. Does not block or gate Phases 1–5, and is now simpler to build than it would have been under v5's coupled architecture.

---

## 24. Product Strategy and Resource Scope

The goal is **not** to reproduce Adobe Document Extract, Azure Document Intelligence, Google Document AI, AWS Textract, or similar platforms feature-for-feature. This project composes existing open-source components (§17) and focuses effort on integration, the unified representation, and evaluation.

The project should not require training a foundation model from scratch, a large GPU cluster, a large engineering team, a paid cloud OCR API, or enterprise infrastructure at the start. Removing OCR from v1 removes the single largest source of GPU/licensing/accuracy-tuning overhead. **v6's diagnostic path (§18.4) is explicitly optional per-page compute, not a new baseline cost** — a resource-conscious implementation can skip it entirely and still be fully spec-compliant.

**Initial product:**

```text
PDF with machine-readable text layer
          ↓
Page-by-page analysis
          ↓
Structured metadata (+ optional diagnostics on gate-failed pages)
          ↓
JSON / JSONL
```

Future commercialization (community/open-source core, hosted API, enterprise self-hosted/private deployment) remains a later delivery-model decision, not a v1 requirement.

---

## 25. Competitive Position

Commercial systems already perform substantial document extraction, including OCR, layout, tables, and reading order — this project should not claim otherwise. Its differentiation is open-source, local/self-hosted operation; model-agnostic, swappable layout/table/vision components; a unified page-level representation with explicit provenance; transparent evaluation; and a clean, explicit input contract rather than an implicit "best effort on anything." These advantages should be validated against existing tools before making strong market claims.

---

## 26. Product Boundary and Success Criteria

The project should be considered technically successful when it can reliably:

1. Accept PDFs with a machine-readable text layer, and cleanly flag pages that lack one (§18.1) as `failed` rather than silently failing or misrepresenting what happened.
2. Normalize all outputs into one documented, versioned coordinate system (§6).
3. Extract text with accurate geometry directly from the native PDF.
4. Detect meaningful layout regions, tables, and visual elements — **on both gate-passed and gate-failed pages**, correctly routed to `elements` or `diagnostic_elements` (§15, §18.4).
5. Produce a defensible reading order for multi-column, RTL, and other complex layouts.
6. Extract native PDF typography accurately when metadata exists, with no fabricated inference in v1.
7. Expose field-level confidence and provenance, distinguishing exact extraction from detected regions.
8. Handle text in any language present in the PDF's text layer.
9. Handle diverse born-digital page designs without hard-coding any one document type.
10. Pass meaningful public-benchmark and internal evaluation tests (§21), including gate-failed test cases.
11. Remain replaceable at the layout, table, and visual-analysis component level.
12. Have a documented dependency/licensing position before any commercial distribution (§27).
13. Add OCR later, if ever, as a pure extension with no schema break and minimal pipeline rework, thanks to the decoupling in §18.1 (§20).

---

## 27. Licensing and Commercialization Readiness

Audit separately, before any commercial release: source-code licenses, pretrained model licenses (layout/table models), model weights, datasets used for training/evaluation, third-party libraries, and fonts/font databases used by any component. A component being open-source does not mean its weights, training data, or commercial-use terms carry identical permissions. Maintain a dependency/license inventory. (Removing OCR from v1 also removes several licensing question marks that would otherwise apply now — deferred to §20's future work.)

---

## 28. Technical Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Mixed coordinate systems (PDF points vs. rendered pixels) | High | Canonical geometry layer + conversion tests |
| Non-axis-aligned elements in born-digital PDFs | Medium | Polygon + rotation support in geometry (§6) |
| Complex reading order | High | Layered geometric + model-based strategy (§10) |
| Cross-model confidence incompatibility | Medium/High | Preserve raw scores; calibrate only with evidence (§16.3) |
| Table structure errors | High | Separate detection vs. structure evaluation (§11, §21) |
| Text-layer quality (garbled text passing the presence check) | Medium/High | Dedicated quality checks and a messy internal eval set (§18.3, §21.1) |
| Partial processing failures | Medium | Page status + structured, per-stage errors (§18) |
| Schema evolution, including eventually adding OCR | High | Explicit schema versioning; OCR fields reserved now (§19, §20.2) |
| **Coupling visual/layout analysis to text-extraction success** *(v6, resolved)* | *Was Medium* | Decoupled via the gate/pipeline split in §18.1 and the diagnostic path in §18.4; no longer an open risk, listed here for traceability |
| Commercial licensing conflicts | High | Dependency/model/data license audit (§27) |
