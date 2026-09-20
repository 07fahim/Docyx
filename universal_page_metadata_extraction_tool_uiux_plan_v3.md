# Universal Page Metadata Extraction Tool — Website UI/UX Plan (v3)

**What changed in v3:** the backend plan (v5) fully removed OCR from the core MVP — the system only accepts PDFs with a machine-readable text layer. This version updates every place in the UI that assumed OCR, image uploads, or scanned-document handling, so the interface makes an honest promise that matches what the backend can actually do. Changes are marked **[v3]**. Earlier fixes from v2 are marked **[v2]** and are unchanged unless noted.

**Patch (v3.1):** the original v3 wording for text-layer failures implied that layout/visual analysis is technically impossible without native text. It isn't — the backend (v6) decouples layout, table, and visual-element detection from the text-layer gate; only the *valid v1 result* requires text, not the underlying visual analysis capability. This patch corrects the failure copy in §7, §21, §22, and §27 to keep the strict product contract without asserting a false architectural limitation. Changes are marked **[v3.1]**.

---

## 1. Product UI Vision

The website is a visual workspace for extracting, inspecting, correcting, and exporting structured metadata from **PDF pages that already contain a machine-readable text layer**. **[v3]**

The primary experience is:

```
Upload → Process → Inspect → Edit → Validate → Export
```

The interface should make the relationship between the document page and its structured JSON representation immediately understandable.

The product is not simply an OCR viewer — in fact, in v1 it contains no OCR at all. **[v3]** The UI should expose the complete extracted page representation, including:

- text
- bounding boxes
- polygons
- rotation
- layout regions
- reading order
- tables
- visual elements
- typography
- language/direction
- confidence
- provenance

---

## 2. Core UX Principles

### 2.1 Visual-first

The uploaded document should always remain visually accessible. Users should be able to understand extracted information directly on the original page rather than reading JSON alone.

### 2.2 JSON remains the source representation

The JSON editor represents the structured extraction result. The visual editor and JSON editor must remain synchronized.

### 2.3 Every extracted object should be inspectable

```
Text
────────────────
Content: Hello world
Type: paragraph
Language: English
Reading order: 4

Geometry
BBox: [120, 240, 380, 65]
Rotation: 0°
Polygon: ...

Confidence
Value: 1.0
Type: Exact          ← [v3] native PDF text is always exact, not a model guess

Provenance
Source: Native PDF   ← [v3]
```

### 2.4 Editing should be reversible

Undo, redo, cancel changes, restore extracted (pre-edit) values — via one shared history per page (§45).

### 2.5 Don't overwhelm users

The raw JSON can be complex. The UI should provide a human-friendly metadata editor while still allowing advanced users to edit the raw JSON directly.

### 2.6 Be honest about the input contract **[v3, new]**

The product only works on PDFs with a real text layer. The UI should say this plainly and early — at Upload, not as a surprising failure after processing. Overpromising ("upload any document") and then failing at the Processing screen erodes trust; underpromising accurately at Upload does not.

---

## 3. Primary User Workflow

```
Landing
   ↓
Upload
   ↓
Document Processing
   ↓
Document Workspace
   ↓
Inspect / Correct
   ↓
Validate
   ↓
Export
```

Returning-user branch **[v2]**:

```
Home / Documents List
   ↓
Open existing document
   ↓
Document Workspace
```

---

## 4. Website Information Architecture **[v2 — Documents added]**

```
Home
│
├── Upload
│
├── Documents            ← [v2] list of previously processed/saved documents
│
├── Workspace
│
│   ├── Page Viewer
│   ├── Element Inspector
│   ├── JSON Editor
│   └── Validation
│
├── Export
│
└── Settings
```

MVP reduction: `Home · Upload · Documents · Workspace · Settings`.

---

## 5. Landing Page

**Hero:**

> Extract structured metadata from every document page.

**Supporting text — updated to state the input contract honestly [v3]:**

> Upload a PDF that already has selectable text — exported from Word, Google Docs, a website, or similar — and inspect its layout, geometry, tables, reading order, typography, and other page-level metadata in one workspace.

Removing the earlier generic "Upload a PDF or image" phrasing avoids implying scanned/image support that doesn't exist in v1.

**Primary CTA:** Upload Document
**Secondary CTA:** View Example

**Product visualization:** a sample document showing highlighted text boxes, layout regions, table boundaries, a selected element, and its JSON representation side by side.

---

## 6. Upload Page **[v3 — format support and options revised]**

```
┌──────────────────────────────────────────────┐
│ Upload Document                              │
│                                              │
│       ┌──────────────────────────┐           │
│       │                          │           │
│       │     Drop file here       │           │
│       │                          │           │
│       │     or Browse            │           │
│       │                          │           │
│       └──────────────────────────┘           │
│                                              │
│ PDF only — must contain selectable text      │
│                                              │
│                  [Process Document]           │
└──────────────────────────────────────────────┘
```

**Supported input [v3]:** PDF only, in v1. The earlier plan listed "PDF, PNG, JPG, TIFF" — those image formats are removed from the accepted-format list entirely, because they inherently have no text layer and the system has no OCR to read them (backend §2.1). This isn't a UI copy change alone: **the upload control should reject PNG/JPG/TIFF at selection time**, with a clear message, rather than accepting the file and only failing later at Processing. A PDF can still turn out to lack a usable text layer — that's discovered during Processing (§7) and is a different, page-level failure (§21), not an upload-time rejection.

Reject-at-upload message:

> This file type isn't supported yet. Upload a PDF that contains selectable text — image files (PNG, JPG, TIFF) require OCR, which isn't available in this version.

**Optional processing settings** (collapsed by default) **[v3, revised]**:

```
Processing Options
   Detect Tables
   Detect Layout
   Reading Order
   Extract Typography
```

`OCR Engine` and `Language` are removed from this panel — there is no OCR engine to select, and language is read directly from the PDF's text layer per element rather than pre-configured by the user.

---

## 7. Processing Screen **[v3 — stages revised]**

```
Processing document

✓ Loading document
✓ Checking text layer
✓ Extracting native text
● Rendering page for layout analysis
○ Detecting layout
○ Detecting tables
○ Calculating reading order
○ Building metadata
```

**[v3]** "Detecting text" (which implied OCR-style text recognition) is replaced with "Checking text layer" followed by "Extracting native text" — this matches what the backend actually does (a presence/quality gate, then direct extraction) and doesn't imply any recognition step is happening. "Rendering page for layout analysis" is shown as its own stage so users understand *why* a page image is being produced even though there's no OCR — it's for the layout/table/visual models (backend §2.2), not text recognition.

For multi-page documents:

```
Processing

Pages
██████████████░░░░░░  72%

Page 8 of 12
```

**Page-level failure during processing [v3.1]:**

> Page 6 has no usable text layer, so it can't produce a valid result. Text and layout extraction completed on the other 11 pages.

The user should still be able to open the document if partial results are available — this is expected and normal (backend §18.2), not an edge case, since real documents are often mixed (a native cover letter with one scanned exhibit page, for example).

---

## 8. Main Document Workspace

Recommended v1 layout — two main panels, inspector as a drawer:

```
┌───────────────────────────┬──────────────────────────────────┐
│                           │                                  │
│       PAGE VIEWER         │         JSON EDITOR              │
│                           │                                  │
│     Document image        │     Structured JSON              │
│                           │     (current page only — §44)     │
│     ┌──────────────┐      │     {                            │
│     │ selected     │      │       "type": "text",            │
│     │ text region  │      │       "geometry": {...}           │
│     └──────────────┘      │     }                            │
│                           │                                  │
└───────────────────────────┴──────────────────────────────────┘
```

---

## 9. Page Viewer

Navigation, zoom, and canvas controls are unchanged from v2. Overlay controls:

```
☑ Text
☑ Layout
☑ Tables
☑ Figures
☑ Reading Order
☐ Confidence
☐ Bounding Geometry
```

---

## 10. Bounding Box Interaction

Create, select, move, resize, delete, and optionally duplicate regions. The system preserves polygon geometry when available rather than converting everything into an axis-aligned rectangle. A region created here has no upstream model output — see §47 for its provenance value.

---

## 11. Text Editing

```
┌───────────────────────────────┐
│ Text                          │
│ ┌───────────────────────────┐ │
│ │ Extracted text here       │ │
│ └───────────────────────────┘ │
│ [Apply] [Cancel]              │
└───────────────────────────────┘
```

Editing text distinguishes extracted value from user-corrected value rather than destroying provenance:

```json
{
  "text": "Corrected text",
  "provenance": {
    "original_text": "Extracted text",
    "modified_by_user": true
  }
}
```

---

## 12. Element Inspector **[v3 — example updated]**

```
ELEMENT

Type
[ Paragraph ▼ ]

Text
[................................]

Language
[ English ▼ ]

Direction
[ LTR ▼ ]

Reading Order
[ 12 ]

Geometry
BBox      120, 340, 540, 72
Rotation  0°

Confidence
Value     1.0
Type      Exact              ← [v3]

Source
Native PDF                   ← [v3]

Engine
PyMuPDF                      ← [v3]

[Apply Changes]
```

**[v3]** The previous example showed an OCR confidence value and an OCR engine name (Surya). Since v1 has no OCR, the default example is now native-PDF text: confidence is always `Exact` / `1.0` for native text, and the source/engine reflect the native extraction library. A layout-detected element (e.g., a table region) looks different — its confidence is meaningfully probabilistic:

```
Type      Table
Confidence
Value     0.91
Type      Detected
Source    Layout Model
Engine    (layout model name)
```

Different element types still expose different fields — a Table shows rows/columns/cells, a Figure shows caption and confidence.

---

## 13. JSON Editor **[v2 — per-page scope, see §44]**

A proper code editor: syntax highlighting, formatting, validation, line numbers, search, error highlighting, collapse/expand, undo/redo.

**Scope for v1: current page only** (§44). Example:

```json
{
  "page_number": 1,
  "status": "ok",
  "elements": []
}
```

Invalid JSON does not silently update the visual page:

> Invalid JSON — changes have not been applied.

An explicit **Apply JSON Changes** action commits valid edits.

---

## 14. Two-Way Synchronization

```
Page → JSON:  draw bbox → geometry changes → JSON updates
JSON → Page:  edit bbox → JSON validated → page overlay updates
Selection:    click box ↔ JSON object highlighted (both directions)
```

---

## 15. JSON ↔ Visual Conflict Handling

If both sides are edited before either is applied, show **Unsaved changes** with explicit options: Apply visual changes / Apply JSON changes / Revert / Cancel.

---

## 16. Element Types

Text, Title, Heading, Paragraph, List, Table, Table Cell, Figure, Image, Caption, Header, Footer, Form Field, Signature, Stamp, Page Number, Other. Extensible; don't create dozens of types initially.

---

## 17. Reading Order UI

```
① Title
② Paragraph
③ Paragraph
④ Figure
⑤ Caption
⑥ Table
```

Users can inspect or manually correct ordering.

---

## 18. Table Editing UI

```
┌──────────┬──────────┬──────────┐
│ Product  │ Quantity │ Price    │
├──────────┼──────────┼──────────┤
│ Apple    │ 2        │ 100      │
├──────────┼──────────┼──────────┤
│ Orange   │ 4        │ 200      │
└──────────┴──────────┴──────────┘
```

Cell-level editing does not need to be fully implemented in the first UI prototype.

---

## 19. Confidence Visualization **[v3 — note added]**

Three modes: Normal, Confidence overlay, Inspect mode.

**[v3]** Because native text confidence is always `Exact` / `1.0` (§12), a confidence overlay applied to text elements alone will show everything as uniformly "fully confident" and won't be very informative in v1 — this is expected, not a bug. The confidence overlay is most useful for **detected** elements: layout regions and table structure, where a real model probability exists. Consider defaulting the confidence overlay to highlight only layout/table elements, with text elements shown neutrally, so the overlay isn't misleadingly flat.

Do not rely only on color — color alone is poor for accessibility.

---

## 20. Provenance UI **[v3 — examples updated]**

```
Extraction

Source: Native PDF
Engine: PyMuPDF
Version: x.x.x
```

For a detected layout/table element:

```
Extraction

Source: Layout Model
Engine: (model name)
Version: x.x.x
Raw confidence: 0.87
```

For user-created elements:

```
Extraction

Source: Manual
Created by user
```

**[v3]** The previous version's example showed `Source: OCR`. Since OCR doesn't exist in v1, that example is removed; `native_pdf`, `layout_model`, `table_model`, and `geometry_inference` are the only active provenance sources a v1 user will ever see, plus `manual` for user-created elements (§47). The backend schema also reserves `ocr` and `visual_inference` for the future OCR extension, but those values should never appear in the v1 UI — if they ever do, that's a bug, not a legitimate state to design a display for yet.

---

## 21. Page Status UI **[v3.1 — failure wording corrected]**

```
Page 6

✕ Failed — no usable text layer

This page has no usable machine-readable text layer, so it
cannot produce a valid v1 extraction result. Scanned or
image-only pages aren't supported in this version.

The page may still be rendered for diagnostics, but its
visual analysis is not treated as a valid v1 result.
```

For a partially-successful page:

```
Page 8

⚠ Partial extraction

Text          ✓
Layout        ✓
Tables        ⚠
Reading Order ✓
```

**[v3.1]** The earlier wording implied that layout/visual analysis is technically impossible without native text, which isn't accurate — the backend can still run layout, table, and visual-element detection on a rendered page even when the text-layer gate fails (backend §18.1/§18.4). Three distinct claims were getting collapsed into one sentence, and this wording separates them:

- **v1 support contract:** a usable text layer is required to produce a valid v1 result — a product decision, stated plainly.
- **Technical capability:** rendering and visual analysis don't require native text — an architecture fact the copy shouldn't contradict, even though the UI doesn't need to promise it either.
- **Current product behavior:** if the gate fails, the page is marked `Failed`, full stop. Any diagnostic layout/visual output the backend may have produced (backend §18.4's `diagnostic_elements`) is **not surfaced in the v1 Workspace** as if it were a normal extraction result — it never populates the Page Viewer overlays, the Element Inspector, or the JSON editor's `elements` array. If a future version exposes that diagnostic output, it belongs in a clearly separate, explicitly-labeled view, never merged into the normal element list.

"No usable text layer" is called out with this much explanation because, given the v1 input contract, it's likely to be the single most common failure reason users encounter. There is still no "Retry" action for this failure, since retrying won't produce a text layer that isn't there (§27).

---

## 22. Multi-Page Documents **[v3 — mixed-document messaging added]**

```
Pages

01 ✓
02 ✓
03 ✓
04 ⚠
05 ✓
06 ✕   ← no text layer
```

**[v3]** Since mixed documents (some pages with a text layer, some without) are an expected, designed-for case rather than an edge case (backend §18.2), the page list should make failed pages easy to spot at a glance and clicking one should immediately explain why (§21) — not require opening a separate error log. Consider a one-line document-level summary at the top of the page list when any pages failed:

> 1 of 12 pages has no usable text layer and can't produce a valid result.

Clicking a page loads it into the workspace. Thumbnails lazy-load for large documents.

---

## 23. Validation

```
Validation

✓ Valid schema
✓ Valid coordinates
✓ All elements have IDs
✓ Reading order valid
⚠ 2 elements have low confidence
⚠ Table on page 4 has incomplete cells
```

Validation distinguishes errors, warnings, and informational messages.

---

## 24. Export **[v2 — gating rule]**

Primary export: JSON (canonical schema). **Export gating:** warnings never block export (shown with a banner); errors do block it, with the Export button disabled and a tooltip pointing to the failing item.

---

## 25. Save and Versioning

Simple save/revert for v1, with the Documents list (§4) as the entry point back into a saved workspace. Version history is a later feature.

---

## 26. Unsaved Changes

> You have unsaved changes.
> Save / Discard / Cancel

---

## 27. Error Handling **[v3 — NO_TEXT_LAYER case added]**

Bad: `Error 500.`

Better, general case:

> Unable to extract page 6 because the PDF could not be rendered.
> [Retry Page]  [View Details]

**No-text-layer case [v3.1]** — deliberately no Retry button, since the problem isn't transient:

> Page 6 has no usable machine-readable text layer, so it cannot produce a valid v1 extraction result. Scanned or image-only pages aren't supported in this version.
> The page may still be rendered for diagnostics, but its visual analysis is not treated as a valid v1 result.
> [View Details]

**Unsupported file at upload [v3]:**

> This file type isn't supported yet. Upload a PDF with selectable text.

For JSON: `Invalid JSON at line 24, column 18.`

---

## 28. Responsive Design

```
Desktop:  Page Viewer | JSON / Inspector
Tablet:   Page Viewer ↕ Inspector / JSON
Mobile:   Page Viewer → Element Inspector → JSON Editor
```

Advanced bbox editing and JSON editing are optimized for desktop.

---

## 29. Accessibility

Keyboard navigation, visible focus states, accessible labels, sufficient contrast, non-color-only status indicators.

```
Ctrl/Cmd + Z        Undo
Ctrl/Cmd + Shift+Z  Redo
Ctrl/Cmd + S        Save
Delete              Delete element
Esc                 Cancel selection
+ / -               Zoom
```

---

## 30. Visual Design Direction

Clean, restrained, technical, spacious, strong hierarchy, minimal decoration, neutral document canvas, clear selection states. The document remains the visual focus.

---

## 31. Design System

Highly readable UI font, distinct from the document's own typography. Reusable components: buttons, dropdowns, tabs, panels, toolbars, badges, tooltips, dialogs, property editors, JSON editor, thumbnails, canvas overlays. Every interactive component defines Default/Hover/Active/Selected/Focused/Disabled/Loading/Error/Warning/Success states.

---

## 32. Main Workspace Toolbar

```
[Select] [BBox] [Polygon] [Pan]   Zoom: − 100% +   [Fit Page]
Overlays ▼      Undo  Redo      Validate  Save  Export
```

---

## 33. Overlay System

```
☑ Text  ☑ Layout  ☑ Tables  ☑ Figures  ☐ Reading Order  ☐ Confidence  ☐ Geometry
```

Independent toggles matter most on dense pages (§46).

---

## 34. Search

Later feature: document search that navigates to and highlights matching elements. Not required for v1.

---

## 35. Annotation Mode

```
Mode:  ● Inspect   ○ Correct   ○ Annotate
```

Inspect (read), Correct (modify model output), Annotate (create ground-truth labels) — valuable later for building an evaluation dataset (backend §21.1's internal eval set could be built this way).

---

## 36. Important Separation: Extraction vs. Editing

```
MODEL OUTPUT → USER CORRECTION → FINAL OUTPUT
```

Never overwrite the original model result without recording that a human changed it.

---

## 37. MVP UI Scope **[v3 — upload contract and failure handling made explicit]**

**Upload**
- **PDF only** — no PNG/JPG/TIFF acceptance in v1 (§6)
- reject unsupported formats at selection time, not after processing

**Documents [v2]**
- flat list: filename, date, page count, status, open into Workspace

**Workspace**
- page viewer, navigation, zoom/pan
- extracted bounding boxes: select, draw, move/resize
- edit text
- basic metadata inspector, reflecting exact-vs-detected confidence (§12, §19)
- JSON editor (current page only, §44)
- JSON ↔ page synchronization
- unified undo/redo (§45)
- **per-page failure display, specifically the no-text-layer case (§21), since it's expected to be the most common failure mode**

**Output**
- validation with export gating (§24)
- JSON export
- save corrections

---

## 38. Post-MVP UI

Polygon editing, rotation editing, table/cell editing, reading-order correction, confidence visualization refinements, provenance inspection, multi-page search, annotation mode, version history, batch processing, Documents list search/filtering, bulk/review-queue correction (§48), and — if the backend's OCR extension (§20 of the backend plan) ships — scanned/image upload support, OCR confidence display, and image-based typography inference display, at that point and not before.

---

## 39. Later Product Features

Collaboration, dataset creation tooling, API integration, enterprise deployment, commercial billing/plans. Not drivers of initial UI design.

---

## 40. Recommended Final User Journey

```
                 ┌──────────────┐
                 │    HOME      │
                 └──────┬───────┘
          ┌─────────────┴─────────────┐
          ▼                           ▼
   ┌──────────────┐            ┌──────────────┐
   │    UPLOAD    │            │  DOCUMENTS   │
   │  (PDF only)  │            └──────┬───────┘
   └──────┬───────┘                   │
          ▼                           │
   ┌──────────────┐                   │
   │  PROCESSING  │                   │
   └──────┬───────┘                   │
          └─────────────┬─────────────┘
                         ▼
        ┌───────────────────────────────┐
        │         WORKSPACE             │
        │  ┌────────────┐ ┌──────────┐ │
        │  │    PAGE    │↔│   JSON   │ │
        │  │   VIEWER   │ │  EDITOR  │ │
        │  └────────────┘ └──────────┘ │
        │          ↕  INSPECTOR         │
        └──────────────┬────────────────┘
                       ▼
                ┌──────────────┐
                │   VALIDATE   │
                └──────┬───────┘
                       ▼
                ┌──────────────┐
                │    EXPORT    │
                └──────────────┘
```

---

## 41. Product Definition

> A visual editor for structured document metadata — for PDFs that already contain text.

Not "an OCR website," and in v1, not an OCR product at all. **[v3]** The key interaction remains:

```
Document page ↔ extracted objects ↔ structured JSON
```

---

## 42. What Not to Build Yet

Subscription/billing UI, complex user management, team collaboration, enterprise administration, mobile-first editing, dozens of export formats, an AI chat assistant, document semantic search, complex dashboards — and, per this version, **any scanned/image upload path or OCR-related UI** until the backend's OCR extension actually ships. **[v3]**

The first milestone remains:

> Upload one PDF with selectable text → extract metadata → visually inspect it → draw/select/edit regions → edit JSON → keep both synchronized → export corrected JSON.

---

## 43. Documents List — Detail **[v2]**

```
Documents

 Name              Pages   Status   Last edited
 ─────────────────────────────────────────────
 invoice_q3.pdf     4      ✓ ok     2h ago
 scan_batch_02.pdf  12     ⚠ partial 1d ago
 report_final.pdf   8      ✓ ok     3d ago

                                     [+ Upload New]
```

v1 needs list, status, click-through, and Upload New only.

---

## 44. JSON Editor Scope: Per-Page, Not Whole-Document **[v2]**

The JSON editor always shows only the currently open page's element array, matching the Page Viewer. Switching pages swaps the buffer; unsaved changes must be flagged (§26) before switching discards or applies them. The full multi-page document envelope is assembled only at Export (§24).

---

## 45. Unified Undo/Redo Model **[v2]**

One undo/redo history per page, shared across the Page Viewer and JSON Editor — not one stack per panel. Every applied change (visual or JSON-originated) pushes one entry with full before/after state. Uncommitted edits aren't part of the stack; only **Apply** actions are undoable.

---

## 46. Dense-Page Rendering Strategy **[v2]**

Render overlays as a vector layer (SVG/canvas), not one DOM node per box. Use spatial indexing for hit-testing once element counts exceed a few hundred. Default to a reduced overlay set on very dense pages, with a one-click "show all."

---

## 47. Provenance for User-Created and User-Modified Elements **[v2, updated in v3 for the reconciled enum]**

**[v3]** The backend's v5 canonical provenance enum for v1 is `native_pdf | layout_model | table_model | geometry_inference`, plus `manual` for UI-created elements (added in v2). `ocr` and `visual_inference` are reserved in the schema but inactive until the backend's OCR extension ships (backend §20) — the UI should never need to render them in v1, and should treat their appearance as a bug rather than a state to design for now.

- Elements the user draws from scratch (§10) get `provenance.source: "manual"`.
- Elements that originated from a model but were edited keep their original source (e.g. `native_pdf`, `layout_model`) and add `modified_by_user: true` (§11).
- The Inspector (§12) and Provenance panel (§20) distinguish all three states:
  - `Native PDF · unedited`
  - `Native PDF · corrected by user` / `Layout Model · corrected by user`
  - `Manual · created by user`

**[v3.1]** The backend's `diagnostic_elements` (backend §18.4, on pages that fail the text-layer gate) are a fourth, separate state that the Workspace does not treat as editable content at all in v1 — they carry `diagnostic: true` and never appear in the Page Viewer, Inspector, or JSON editor's normal element flow (§21). There is nothing to reconcile here with user-editing provenance, since a page with `status: "failed"` never reaches the point of being correctable in the Workspace — see §21 for the full reasoning.

---

## 48. Bulk / Review-Queue Correction Mode **[v2 — design note for post-MVP]**

Not MVP scope, but two v1 decisions anticipate it: the Confidence overlay (§19) and Inspector (§12) should treat "elements below a confidence threshold" as a queryable set, not just a visual highlight; a small set of keyboard shortcuts (`N`/`P` for next/previous low-confidence element, `Enter` to accept) should be reserved now even if unbuilt, so the correction flow can grow into this mode without renumbering shortcuts later.

---

## 49. Change Log

| Version | Area | Change |
|---|---|---|
| v2 | Information architecture | Added Documents list (§4, §43) — no prior way back into a saved document |
| v2 | JSON editor | Scoped to current page only, not the whole document envelope (§44) |
| v2 | Undo/redo | One shared history per page instead of two competing stacks (§45) |
| v2 | Dense pages | Vector overlay layer + spatial hit-testing, named as a rendering risk (§46) |
| v2 | Provenance | Added `manual` source for user-drawn elements (§47) |
| v2 | Export | Errors block export, warnings don't (§24) |
| v2 | Correction workflow | Noted bulk/review-queue mode for post-MVP, designed for now (§48) |
| v3 | Upload | PDF-only accepted format; PNG/JPG/TIFF rejected at selection time, not after processing (§6) |
| v3 | Upload | Landing/upload copy states the text-layer requirement honestly instead of implying scanned support (§5, §6) |
| v3 | Processing | Stage list changed from OCR-flavored "Detecting text" to "Checking text layer" + "Extracting native text" (§7) |
| v3 | Processing options | Removed "OCR Engine" and "Language" selectors — no OCR to configure, language is read per-element from the text layer (§6) |
| v3 | Confidence | Native text is always `Exact`/1.0; confidence overlay is only meaningful for detected (layout/table) elements (§12, §19) |
| v3 | Provenance | Removed the OCR example throughout; `native_pdf`/`layout_model`/`table_model`/`manual` are the only sources a v1 user ever sees (§12, §20, §47) |
| v3 | Page status | "No selectable text found" given its own explicit, no-retry failure message, since it's expected to be the most common failure mode (§21, §27) |
| v3 | Multi-page documents | Mixed documents (some pages fail, some succeed) framed as expected behavior with a document-level summary line, not a hidden edge case (§22) |
| v3 | Post-MVP | Any scanned/image upload path or OCR UI explicitly deferred until the backend's OCR extension ships (§38, §42) |
| v3.1 | Page status / errors | Corrected wording that implied layout/visual analysis is technically impossible without text — separated the v1 support contract, the technical capability, and current product behavior into distinct statements (§21, §27) |
| v3.1 | Processing / multi-page | Aligned failure copy in §7 and §22 with the corrected §21 wording |
| v3.1 | Provenance | Noted that backend `diagnostic_elements` (§18.4) are a separate, non-editable state that never enters the normal Workspace flow (§47) |
