# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

No build step, no packaging (`pyproject.toml` does not exist). Run everything through the venv interpreter:

```bash
.venv/Scripts/python.exe -m pytest -q            # full suite (~65 tests, ~2s)
.venv/Scripts/python.exe -m docyx.schema.contract --write   # regenerate schema/v1.2.json after a schema change
.venv/Scripts/python.exe scripts/measure_struct_tree.py CORPUS_DIR  # tagged-PDF prevalence
.venv/Scripts/python.exe -m pytest tests/test_analysis.py::test_reading_order_sorts_top_to_bottom -v
```

There is no linter or formatter configured. `requirements.txt` omits `pydantic`, which every module imports — it arrives transitively via `layoutparser`. Add it explicitly if you touch dependencies.

## Architecture

Page-level PDF metadata extraction. One PDF in → one `Document` (schema v1.2) out, a list of `Page`s each holding `Element`s.

The whole design turns on one decoupling: **the text-layer gate decides whether a page produces valid output, but never blocks rendering or visual detection.** [pipeline/extractor.py](docyx/pipeline/extractor.py) is where this is enforced — read it first. Per page, in order:

1. `TextLayerGate.check_page` → pass/fail
2. `PDFRenderer.render_page` → PNG bytes at 150 DPI — **unconditional**, runs even on gate-failed pages
3. `LayoutAnalyzer` + `TableAnalyzer` + `VisualAnalyzer` on the image — **unconditional**, they only need pixels. Each runs inside `_safely`; a detector that raises records a warning and yields no elements rather than killing the page.
4. Branch on the gate:
   - **pass** → `NativeTextExtractor.extract_page` + detections → `ReadingOrderCalculator.calculate` → `elements`. Status is `ok`, or `partial` if any detector warned.
   - **fail** → `status: failed`, `elements: []`, detections go to `diagnostic_elements`, gate error into `errors`

Reading order is the only stage genuinely dependent on text, which is why it sits inside the pass branch.

### Invariants

- **Coordinates:** top-left origin, reference pixels at **150 DPI**. PDF points are multiplied by `SCALE` from [core/constants.py](docyx/core/constants.py) at every boundary — never re-derive `150/72` locally. Anything crossing into an `Element` must already be in 150-DPI space. The one deliberate exception is `Typography.font_size`, which stays in points.
- **Geometry is unified:** every element type uses the same `Geometry` — required `bbox`, optional `polygon`, optional `rotation`. Do not add per-type geometry shapes.
- **Confidence is two-tier:** native text is always `value=1.0, type=exact`. Detected elements (layout, table, visual) carry probabilistic `detected` confidence. `inferred` is reserved for OCR (phase 6) and must stay unused.
- **Provenance sources active in v1:** `native_pdf`, `layout_model`, `table_model`, `geometry_inference`, `manual`. `ocr` and `visual_inference` exist in the enum but must stay unused.
- **Mixed documents are normal.** Partial results, never reject the whole document.

### Detector injection

Every analyzer takes an optional `detector` and falls back to a stub returning `[]`, so the pipeline runs end to end without model weights. Tests inject fakes through this seam; real models plug in identically. The score populates both `confidence.value` and `provenance.raw_confidence`.

| Analyzer | Detector returns | Provenance |
|---|---|---|
| `LayoutAnalyzer` | `List[Tuple[BoundingBox, float]]` | `layout_model` |
| `TableAnalyzer` | `List[TableDetection]` (bbox, score, `cells`) | `table_model` |
| `VisualAnalyzer` | `List[VisualDetection]` (bbox, kind, score) | `geometry_inference` |

`VisualAnalyzer` is the exception: its fallback is a **real OpenCV heuristic**, not an empty stub. It finds `rule` and `figure` elements via morphology. A rule must be both long (`min_rule_ratio`) and thin (`max_rule_thickness`) — without the thinness bound a solid filled block survives the directional opening and is misreported as a rule, suppressing the figure underneath it. Those constructor knobs are the tuning surface.

Table structure lives in `Element.children`: a `table` holds `table_cell` children, each with a `grid` (`row`, `column`, `row_span`, `column_span`). `cells` may be empty when a detector finds the outline but cannot resolve structure.

### Reading order

Only `text` elements are numbered (`ORDERABLE_TYPES` in [reading_order.py](docyx/analysis/reading_order.py)). Containers — `layout_region`, `table` — and cells get `reading_order: None`, because numbering a table alongside the text inside it interleaves a box with its own contents. All elements are still returned geometrically sorted.

The sort is a raster sort on `(y, x)`, **not** an XY-cut despite what earlier docstrings claimed: multi-column pages interleave their columns line by line. Upgrading to a recursive projection-profile split is the known path.

### The schema is a published contract

`schema/v{version}.json` is generated from the models and committed. [tests/test_schema_contract.py](tests/test_schema_contract.py) fails if they drift — after an intentional schema change, regenerate with `python -m docyx.schema.contract --write` and decide whether §19 requires a version bump. `v1.1.json` is kept as the record of what the phase branches emit; `v1.2.json` is current (warnings became structured `PageIssue` records).

`PageIssue` (code/stage/message) carries both errors and warnings, so consumers branch on a stable `code`, never on message text.

### The text-layer gate has two stages

Presence, then quality (§18.3). A page with a text layer that decodes badly — subsetted fonts, no usable ToUnicode — still *passes* (its text is returned) but carries a `TEXT_LAYER_SUSPECT` warning that degrades it to `partial`. The heuristic measures the share of `U+FFFD` and private-use-area characters; `suspect_ratio` is the knob. It is validated against unit cases and a faked reader only — PyMuPDF's writer sanitizes unmappable codepoints on insert, so a real garbled fixture cannot be synthesized in-process.

### Markdown export doubles as an evaluation instrument

[docyx/export/markdown.py](docyx/export/markdown.py) reconstructs prose from the page representation. Scrambled output is the fastest available signal that reading order is wrong — which is why `test_two_column_page_reads_down_each_column` is a **strict xfail**: it documents the target behaviour and flips to passing when reading order learns column detection. Headings are inferred from font size because layout classification is still a stub; a real layout model's types should take precedence when one lands.

### Known gaps

- Reading order is single-column only (above).
- `figure` detection is a contour heuristic and will merge dense text into blocks on some layouts; rules are reliable. Inject a real figure head via `detector` when precision matters.
- No OCR, per v1 scope. `ocr` / `visual_inference` provenance and `inferred` confidence stay unused until phase 6.

## Planning docs

`.planning/` (GSD workflow: `ROADMAP.md`, `STATE.md`, per-phase dirs) tracks the 6-phase roadmap; phases 1–3 are implemented. The two root markdown plans are the authoritative spec and cross-reference each other by section number — read together, neither is self-contained:

- `universal_page_document_metadata_extraction_plan_v6.md` — architecture, pipeline, schema
- `universal_page_metadata_extraction_tool_uiux_plan_v3.md` — the phase-5 workspace UI

`AGENTS.md` is **stale** — it claims the repo is planning-only with no code. Its v1 scope constraints and architecture notes are still accurate; ignore the "no code yet" framing.

## v1 scope limits (non-negotiable)

PDF-only input, reject non-PDF at upload. No OCR, no model weights for OCR, no GPU. Requires a machine-readable text layer for `ok` status.
