# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

No build step, no packaging (`pyproject.toml` does not exist). Run everything through the venv interpreter:

```bash
.venv/Scripts/python.exe -m pytest -q            # full suite (105 tests, ~55s)
.venv/Scripts/python.exe -m docyx.schema.contract --write   # regenerate schema/v1.3.json after a schema change
.venv/Scripts/python.exe scripts/measure_struct_tree.py CORPUS_DIR  # tagged-PDF prevalence
.venv/Scripts/python.exe -m pytest tests/test_analysis.py::test_reading_order_sorts_top_to_bottom -v
```

There is no linter or formatter configured.

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

### Text granularity

Extraction is at **line** granularity (§5), not span. `NativeTextExtractor` joins a line's spans back together — PyMuPDF emits inter-word gaps as their own spans, so plain concatenation reproduces the line exactly, which is why blank spans must **not** be skipped. Per-span extraction split lines at every inline citation and superscript, and those fragments then scattered during ordering. Typography is taken from the longest span so a leading superscript can't misreport the line. Verified across 469k characters of real PDFs: the character multiset matches PyMuPDF's own extraction exactly.

### Reading order

Only `text` elements are numbered (`ORDERABLE_TYPES` in [reading_order.py](docyx/analysis/reading_order.py)). Containers — `layout_region`, `table` — and cells get `reading_order: None`, because numbering a table alongside the text inside it interleaves a box with its own contents. All elements are still returned geometrically sorted.

Recursive **XY-cut** ([reading_order.py](docyx/analysis/reading_order.py)). A block is cut along blank channels: columns first, since a column runs top-to-bottom before the next begins. A full-width heading or figure caption blocks the vertical cut, which forces a horizontal cut and so keeps it above the columns beneath. Blocks that resist cutting fall back to **banding** — group by vertical overlap, read left to right — which keeps a bold run-in heading ahead of its paragraph despite a fractionally higher bbox.

Cuts use text elements only: a full-width rule would otherwise bridge the gutter and defeat every column cut on the page.

Three guards, each added because removing it regressed a measured case:

| Guard | Why |
|---|---|
| `MIN_COLUMN_HEIGHT_RATIO` 0.5 | A gutter is a channel running *down* a block. Two isolated lines at different heights otherwise look like columns. |
| `MIN_COLUMN_WIDTH_RATIO` 0.25 | Table columns are narrow. Without this, a results table reads downwards — cost 64 points on one GPT-3 page. 0.25 not 0.30 so three-column layouts survive. |
| `ROW_GAP_FACTOR` 1.5, widest gap only | Cutting at *every* gap shatters a two-column body into strips that each still hold both columns. |

Graded against hand-labelled ground truth in `.corpus/truth/`, not against another tool:

```bash
PYTHONPATH=. .venv/Scripts/python.exe scripts/dump_lines.py .corpus/x.pdf 3   # labelling worksheet
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_reading_order.py        # tau + adjacency
PYTHONPATH=. .venv/Scripts/python.exe scripts/compare_tools.py               # vs Docling
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_speed.py               # wall clock
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_bidi.py                # RTL per producer
```

**What the harness does and does not establish** (audited; read before quoting a number):

- **Quote `tau` and `adj` together.** `adj` is nearly blind to the failure it looks like it catches: swap a page's top and bottom halves — unreadable — and adj still scores ~0.99. Only tau collapses. Pinned by `test_adjacency_is_blind_to_a_swapped_page_but_tau_is_not`. Gain over naive is **+0.158 tau / +0.321 adj**; quoting only the larger is cherry-picking.
- **The Docling comparison does not show Docyx ordering better.** 100% of Docling's current deficit is page furniture it declines to emit (the arXiv stamp, a page number) — zero characters are misordered. The supported claim is "neither tool made a detectable ordering error on these pages". The Docyx column is near-tautological: the reference is built from the same lines Docyx orders.
- **Speed is not a like-for-like ratio.** Default `DocyxPipeline()` runs *stub* layout and table detectors; a full stack attempts strictly more work. Median 0.221 s/page (range 0.140–0.267) for native extraction + render, no models.
- **Effectively 4 independent documents**, 3 of them two-column arXiv preprints — the layout family XY-cut was designed for. `nasa_budget.pdf`, `rfc9110.pdf` and every Word-produced non-Latin file are present but unlabelled.
- **Extraction fidelity is ungradeable here.** The labeller can only permute Docyx's own line list, so over-segmentation, under-segmentation and wrong line text cannot be scored. A dropped line trips the checksum and prompts re-labelling rather than lowering the score.

**Re-run `measure_reading_order.py` before touching the thresholds.** Currently 1.000 tau / 0.991 adjacency on 6 pages — but that is 3 pages of English academic papers, which is an instrument, not a benchmark. Extend it before claiming anything.

The superseded metric was *agreement with PyMuPDF* (71.6% → 86.0%). Retired because it is circular: PyMuPDF is a dependency, so the ceiling was "equal PyMuPDF", and the two-column pages where XY-cut legitimately beats it scored as regressions.

Truth files record a hand-verified order plus a checksum of the line inventory; the scorer fails loudly if extraction changes what the lines are, or if an order is not a permutation. **Forms are deliberately absent** — a tax form's 488 lines have no unambiguous linear order, so labelling one would invent truth rather than record it.

Column order respects `direction`: `_is_rtl` takes a majority vote over each block's elements, so an RTL block reads its columns right to left and a mixed page resolves per block.

`direction` comes from the line's direction vector (vertical vs horizontal) plus the **Unicode bidi category of the characters**. It deliberately does *not* use the span's `bidi` embedding level: measured on `.corpus/wiki_ar.pdf`, every Arabic span reports `bidi=0`, because the generator baked visual order into the glyph stream and discarded the levels. Trusting it classified all 74 elements on an Arabic page as LTR and left this whole RTL path dead on the documents it exists for.

`Direction.TTB` text is pulled **out of the cut geometry** and read after the horizontal flow. One vertically-set line — the arXiv stamp down a paper's left margin — has a bbox as tall as the whole text body, so leaving it in bridges the gutter and defeats every column cut on the page. Same failure mode as a full-width rule, same remedy. Measured: this alone cost 0.22 coverage on `arxiv_bert.pdf` p0.

**Floats are the known ceiling.** `.corpus/truth/wiki_ar.p6.json` scores 0.946 adjacency, and every remaining break is one figure caption whose three lines interleave by `y` with the body text beside them. Geometry cannot separate a float from body prose: the caption is 53px tall against a 1527px block, so `MIN_COLUMN_HEIGHT_RATIO` correctly refuses to call it a column. **Do not loosen that threshold to chase this page** — it would start treating short runs as columns everywhere and cost the pages now at 1.000. The fix is a layout model emitting `caption` regions through the `detector` seam. Truth files use the DocLayNet convention: a caption reads as a contiguous unit, in place, never interleaved.

The wide-table failure ("tabular text cuts into columns and reads down rather than across") **did not reproduce** when measured. `arxiv_gpt3.pdf` p7 is an 8-column table with wide inter-column gaps — the exact shape that should break it — and XY-cut reads it correctly row-wise, 1.000/1.000, where the naive baseline gets 0.832. `MIN_COLUMN_WIDTH_RATIO` 0.25 is doing the work it was added for. Treat this as an unverified risk on denser tables, not a known defect.

### The schema is a published contract

`schema/v{version}.json` is generated from the models and committed. [tests/test_schema_contract.py](tests/test_schema_contract.py) fails if they drift — after an intentional schema change, regenerate with `python -m docyx.schema.contract --write` and decide whether §19 requires a version bump. Older versions are kept as the record of what earlier branches emit: `v1.1` (pre-`PageIssue`), `v1.2` (warnings became structured `PageIssue` records). `v1.3` is current — `Element.direction` replaced per-element `language`.

`PageIssue` (code/stage/message) carries both errors and warnings, so consumers branch on a stable `code`, never on message text.

### The text-layer gate has two stages

Presence, then quality (§18.3). Quality now covers two independent failures, and `GateResult` carries one warning, so the more severe (garbled text) wins.

Run `scripts/measure_bidi.py` before trusting any RTL or Indic claim: it groups the corpus by `/Producer`, because **bidi and reordering behaviour is a property of the writing tool, not the script**. It warns on a producer monoculture, which is how the first version of these findings turned out to be Chrome-specific until Word was added.

**`COMBINING_MARK_ORDER`** — the text layer is in glyph order, not logical order. Indic and SE-Asian scripts reorder on display (in বাংলা the vowel sign is typed after its consonant and drawn before it), and a dependent vowel sign can never legitimately begin a word. Measured: **Word-produced Bengali 8.3% word-initial marks, the same language from Chrome 0%**, Arabic and Latin 0%. Every character decodes and the multiset is intact — only one nukta is lost — so `TEXT_LAYER_SUSPECT`'s replacement-character heuristic is blind to it. This was silently returning scrambled Bengali as `ok` with `confidence 1.0 / exact`.

**`RTL_VISUAL_ORDER`** — right-to-left text stored in *visual* rather than logical order. Confirmed across **two independent producers** (Chrome/Skia and Microsoft Word 2021), neither of which emits a non-zero `bidi` level, so this is how PDF works rather than one tool's quirk. Arabic words come back correctly, but bidi-neutral runs (digits, brackets, Latin) are reversed: `.corpus/wiki_ar.pdf` yields `]5[)2021(` where the document reads `(2021)[5]`. Recovering logical order needs the bidi algorithm run backwards, which is ambiguous and lossy — so the page is degraded to `partial` rather than returning scrambled text as `exact` with `confidence 1.0`. Detected by mirrored delimiters on majority-RTL lines only; verified zero false positives across 61 English pages and 8 Bengali. **This means byte-exactness is exact *to PyMuPDF*, not to the document's meaning — a distinction invisible in Latin scripts.**

A page with a text layer that decodes badly — subsetted fonts, no usable ToUnicode — still *passes* (its text is returned) but carries a `TEXT_LAYER_SUSPECT` warning that degrades it to `partial`. The heuristic measures the share of `U+FFFD` and private-use-area characters; `suspect_ratio` is the knob. It is validated against unit cases and a faked reader only — PyMuPDF's writer sanitizes unmappable codepoints on insert, so a real garbled fixture cannot be synthesized in-process.

### Markdown export doubles as an evaluation instrument

[docyx/export/markdown.py](docyx/export/markdown.py) reconstructs prose from the page representation. Scrambled output is the fastest available signal that reading order is wrong — `test_two_column_page_reads_down_each_column` was a strict xfail until XY-cut landed and now passes; keep it as the canary. Headings are inferred from font size because layout classification is still a stub; a real layout model's types should take precedence when one lands.

### Real models (optional)

`pip install -r requirements-models.txt` then inject:

```python
from docyx.analysis.detectors.table_transformer import TableTransformerDetector
DocyxPipeline(table_analyzer=TableAnalyzer(detector=TableTransformerDetector()))
```

**Table Transformer is an object detector, not OCR** — it emits row/column/table *boxes* from the page image and never reads a character from pixels (§2.2). Cell text is joined from the native layer by position in `_populate_cell_text`, so it stays `1.0 / exact` and `ocr`/`visual_inference` remain unused. Verified on a real IRS form: 731 text elements, all `native_pdf`.

The stack is optional by design — core stays at 4 light dependencies (§24). Measured ~1.5s/page on CPU after a one-off weight load.

Detectors may declare an `engine` attribute; analyzers report it as `provenance.engine` instead of their own heuristic name. Attributing a model's output to the heuristic it replaced would make §26.11's swappability claim unverifiable.

### Known gaps

- `figure` detection is a contour heuristic. Dense text used to be misreported as figures (434 of them in a 114-page RFC); a component-density filter now rejects candidates that fragment like text. Inject a real figure head via `detector` when precision matters.
- No OCR, per v1 scope. `ocr` / `visual_inference` provenance and `inferred` confidence stay unused until phase 6.

## Planning docs

`.planning/` (GSD workflow: `ROADMAP.md`, `STATE.md`, per-phase dirs) tracks the 6-phase roadmap; phases 1–3 are implemented. The two root markdown plans are the authoritative spec and cross-reference each other by section number — read together, neither is self-contained:

- `universal_page_document_metadata_extraction_plan_v6.md` — architecture, pipeline, schema
- `universal_page_metadata_extraction_tool_uiux_plan_v3.md` — the phase-5 workspace UI

`AGENTS.md` is **stale** — it claims the repo is planning-only with no code. Its v1 scope constraints and architecture notes are still accurate; ignore the "no code yet" framing.

## Licensing

**AGPL-3.0** (`LICENSE`), because PyMuPDF is AGPL-or-commercial and the project's stated differentiator is open-source self-hosted operation. See [LICENSING.md](LICENSING.md) for the dependency inventory and what the choice forecloses — notably a proprietary hosted API or closed enterprise deployment, both of which appear in §24's commercialization sketch.

PyMuPDF is confined to [docyx/pdf/](docyx/pdf/); everything outside depends on the protocols in [docyx/pdf/protocols.py](docyx/pdf/protocols.py). **Keep it that way** — that containment is what makes the licence decision reversible for the cost of one package. Any injected layout/table model brings its own licence; that audit is still outstanding.

## v1 scope limits (non-negotiable)

PDF-only input, reject non-PDF at upload. No OCR, no model weights for OCR, no GPU. Requires a machine-readable text layer for `ok` status.
