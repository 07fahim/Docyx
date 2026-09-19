# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

No build step, no packaging (`pyproject.toml` does not exist). Run everything through the venv interpreter:

```bash
PYTHONPATH=. .venv/Scripts/python.exe -m docyx file.pdf -o out.json   # CLI
PYTHONPATH=. .venv/Scripts/python.exe -m docyx *.pdf -o results/ -f markdown
PYTHONPATH=. .venv/Scripts/python.exe -m docyx scan.pdf --ocr ben   # optional OCR, see below
PYTHONPATH=. .venv/Scripts/python.exe -m docyx paper.pdf --layout --tables -f bundle -o out/
.venv/Scripts/python.exe -m pytest -q            # full suite (153 tests, ~10s)
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
   - **fail** → `status: failed`, `elements: []`, detections go to `diagnostic_elements`, gate error into `errors`. Unless an `OCRAnalyzer` is injected and recognises something, in which case the page becomes `partial` with `inferred` text — see **OCR** below.

Reading order is the only stage genuinely dependent on text, which is why it sits inside the pass branch.

### Invariants

- **Coordinates:** top-left origin, reference pixels at **150 DPI**. PDF points are multiplied by `SCALE` from [core/constants.py](docyx/core/constants.py) at every boundary — never re-derive `150/72` locally. Anything crossing into an `Element` must already be in 150-DPI space. The one deliberate exception is `Typography.font_size`, which stays in points. `bold`/`italic`/`serif`/`monospace`/`superscript` are **derived** from the `flags` bitfield rather than stored beside it, so the two cannot drift; they are `None` rather than `False` when the PDF said nothing, because "not stated" and "not bold" are different claims.
- **Geometry is unified:** every element type uses the same `Geometry` — required `bbox`, optional `polygon`, optional `rotation`. Do not add per-type geometry shapes.
- **Confidence is three-tier:** native text is always `value=1.0, type=exact`. Detected elements (layout, table, visual) carry probabilistic `detected` confidence. **`inferred` belongs to OCR and to nothing else** — that split is what lets a consumer tell read text from recognised text without parsing engine names.
- **Provenance sources active:** `native_pdf`, `layout_model`, `table_model`, `geometry_inference`, `manual`, and `ocr` (only when an OCR detector is injected). `visual_inference` exists in the enum but must stay unused.
- **Mixed documents are normal.** Partial results, never reject the whole document.

### Detector injection

Every analyzer takes an optional `detector` and falls back to a stub returning `[]`, so the pipeline runs end to end without model weights. Tests inject fakes through this seam; real models plug in identically. The score populates both `confidence.value` and `provenance.raw_confidence`.

| Analyzer | Detector returns | Provenance |
|---|---|---|
| `LayoutAnalyzer` | `List[Tuple[BoundingBox, float]]` | `layout_model` |
| `TableAnalyzer` | `List[TableDetection]` (bbox, score, `cells`) | `table_model` |
| `VisualAnalyzer` | `List[VisualDetection]` (bbox, kind, score) | `geometry_inference` |
| `OCRAnalyzer` | `List[OCRLine]` (bbox, text, score) | `ocr` |

`VisualAnalyzer` is the exception: its fallback is a **real OpenCV heuristic**, not an empty stub. It finds `rule` and `figure` elements via morphology. A rule must be both long (`min_rule_ratio`) and thin (`max_rule_thickness`) — without the thinness bound a solid filled block survives the directional opening and is misreported as a rule, suppressing the figure underneath it. Those constructor knobs are the tuning surface.

**The output states its own coordinate system.** `Page.coordinate_system` carries `{origin, units, reference_resolution}` (§4). The invariant was enforced at every boundary and documented here, but never emitted — a consumer reading the JSON had to already know that `x: 236.29` meant 150-DPI top-left pixels, or guess. It is a field rather than a convention so that rendering at another DPI becomes a value change instead of a silent reinterpretation of every box ever exported.

Table structure lives in `Element.children`: a `table` holds `table_cell` children, each with a `grid` (`row`, `column`, `row_span`, `column_span`). `cells` may be empty when a detector finds the outline but cannot resolve structure. `is_header` distinguishes a header cell from body (§11): Table Transformer detects it as its own class, and the grid builder was binding the flag to `_` and discarding it while the module docstring claimed otherwise.

### Text granularity

Extraction is at **line** granularity (§5), not span. `NativeTextExtractor` joins a line's spans back together — PyMuPDF emits inter-word gaps as their own spans, so plain concatenation reproduces the line exactly, which is why blank spans must **not** be skipped. Per-span extraction split lines at every inline citation and superscript, and those fragments then scattered during ordering. Typography is taken from the longest span so a leading superscript can't misreport the line. **A line that mixes styles also keeps its runs in `children` as `text_span` elements** — the dominant span is an honest summary of a uniform line and a lossy one otherwise, since `Note: and the rest of the sentence` reports `bold: false` and the bold vanishes. Measured: 3.6% of corpus lines mix bold with non-bold, 9.0% mix any two styles. Uniform lines get no children, because restating a line's own typography beneath it would roughly double the output to say nothing. Spans are not orderable and live in `children`, so a line and its own fragments never occupy separate reading positions. Verified across 469k characters of real PDFs: the character multiset matches PyMuPDF's own extraction exactly.

### Alignment, indent, line height (§7)

`Element.layout` (`TextLayout`), a separate object from `Typography` on purpose: typography is *carried verbatim from the PDF*, these are *measured from geometry*, and mixing read values with computed ones inside an object whose claim is "what the file said" would quietly break the provenance story.

- **`indent` and `line_height`** are plain measurements against the PyMuPDF block and are always present. `indent` is from the *leading* edge, so an RTL paragraph measures from the right — reporting its left gap would invert the meaning.
- **`alignment` needs a real paragraph box, and a PyMuPDF block is not one.** Two attempts to derive it from blocks were both confidently wrong on `arxiv_attention` p2: one block holds `3.1` and `Encoder and Decoder Stacks` together, so the heading measured `right`; against modal edges instead, a short last line measured `justify`. It is therefore computed in the pipeline against **layout regions**, and is `None` without a layout model. An absent value beats a wrong one here.

Three rules the measurements forced, each pinned by a test:

| rule | why |
|---|---|
| 3+ lines before reporting anything | two lines can show any two arbitrary edges |
| modal edges, ties toward the widest | tie-breaking toward the *short* line made a justified paragraph's last line report `justify` — the one line that never is |
| `justify` needs 2+ lines flush both sides | otherwise the widest line of a *centred* block claims `justify` on its own |

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

- **Quote `tau` and `adj` together.** `adj` is nearly blind to the failure it looks like it catches: swap a page's top and bottom halves — unreadable — and adj still scores ~0.99. Only tau collapses. Pinned by `test_adjacency_is_blind_to_a_swapped_page_but_tau_is_not`. Gain over naive is **+0.146 tau / +0.329 adj**; quoting only the larger is cherry-picking.
- **The Docling comparison does not show Docyx ordering better.** 100% of Docling's current deficit is page furniture it declines to emit (the arXiv stamp, a page number) — zero characters are misordered. The supported claim is "neither tool made a detectable ordering error on these pages". The Docyx column is near-tautological: the reference is built from the same lines Docyx orders.
- **Speed is not a like-for-like ratio.** Default `DocyxPipeline()` runs *stub* layout and table detectors; a full stack attempts strictly more work. Median 0.221 s/page (range 0.140–0.267) for native extraction + render, no models.
- **8 pages across 5 documents**, 3 of them two-column arXiv preprints — the layout family XY-cut was designed for. Still unlabelled: `rfc9110.pdf`, and every *Word-produced* non-Latin file, which is the producer monoculture `measure_bidi.py` warns about.
- **Multi-line table cells are the known failure.** `nasa_budget.pdf` p88 scores 0.808/0.706 — the lowest in the set and deliberately so. Its table cells wrap over up to ten lines, so the correct order is cell by cell; `arxiv_gpt3.pdf` p7's cells are single-line, so the correct order there is row-wise. Geometry alone cannot satisfy both without knowing where cell boundaries are, which is what a table model provides. Do not tune thresholds at this — it is the same class as the caption float.
- **Extraction fidelity is ungradeable here.** The labeller can only permute Docyx's own line list, so over-segmentation, under-segmentation and wrong line text cannot be scored. A dropped line trips the checksum and prompts re-labelling rather than lowering the score.

**Re-run `measure_reading_order.py` before touching the thresholds.** Currently 0.976 tau / 0.957 adjacency on 8 pages across 5 documents and 3 scripts. Still an instrument rather than a benchmark: 3 of the 8 are two-column arXiv preprints, the layout family XY-cut was built for.

The superseded metric was *agreement with PyMuPDF* (71.6% → 86.0%). Retired because it is circular: PyMuPDF is a dependency, so the ceiling was "equal PyMuPDF", and the two-column pages where XY-cut legitimately beats it scored as regressions.

Truth files record a hand-verified order plus a checksum of the line inventory; the scorer fails loudly if extraction changes what the lines are, or if an order is not a permutation. **Forms are deliberately absent** — a tax form's 488 lines have no unambiguous linear order, so labelling one would invent truth rather than record it.

Column order **and band order** respect `direction`: `_is_rtl` takes a majority vote over each block's elements, so an RTL block reads its columns right to left and a mixed page resolves per block. Banding ignored it until measured — that reversed every multi-element RTL row and put `wiki_ar.pdf` p6 *below* the naive baseline.

**`script`, not `language` (§7).** The spec asks for `language`; a PDF carries none, so `"en"` could only come from statistical detection — a guess, which cannot sit in a schema where every value declares its own reliability. Script *is* in the characters: Unicode names U+0995 `BENGALI LETTER KA`, so the first word of the codepoint's name is the answer, with no table and no model. It cannot tell English from French and does not pretend to; it tells Bengali from Arabic from Latin, which is what these documents turn on. Verified: `wiki_ar` p6 all `arabic`, `word_bn` p0 all `bengali`, `arxiv_attention` p0 all `latin`.

`direction` comes from the line's direction vector (vertical vs horizontal) plus the **Unicode bidi category of the characters**. It deliberately does *not* use the span's `bidi` embedding level: measured on `.corpus/wiki_ar.pdf`, every Arabic span reports `bidi=0`, because the generator baked visual order into the glyph stream and discarded the levels. Trusting it classified all 74 elements on an Arabic page as LTR and left this whole RTL path dead on the documents it exists for.

`Direction.TTB` text is pulled **out of the cut geometry** and read after the horizontal flow. One vertically-set line — the arXiv stamp down a paper's left margin — has a bbox as tall as the whole text body, so leaving it in bridges the gutter and defeats every column cut on the page. Same failure mode as a full-width rule, same remedy. Measured: this alone cost 0.22 coverage on `arxiv_bert.pdf` p0.

**A layout model was tried against the ceiling and did not lift it.** `DocLayNetDetector` now exists and works, so the long-standing claim "the fix is a layout model emitting `caption` regions through the `detector` seam" became testable. It failed twice over:

- On `wiki_ar.pdf` p6 the model emits **no `Caption` region at all**. It does separate the caption — lines 18, 20 and 22 fall inside *no* region while body lines 19, 21 and 23 all land in one — so the information is there, just not as a label.
- Grouping lines by region and reading each group contiguously (`scripts/experiment_region_order.py`) scores **worse**: `arxiv_bert.p0` 1.000 → 0.519 tau, `arxiv_bert.p3` 1.000 → 0.473, and `wiki_ar.p6` 0.999 → 0.985 with adjacency unchanged at 0.946. It wrecks two-column pages and does not fix the float it was built for.

The experiment's group ordering is deliberately naive — groups sorted by `(min y, min x)`, which cannot express columns — so the *idea* is not disproven, only this implementation. But the result that matters is the second one: it left `wiki_ar.p6` adjacency exactly where it was. A mechanism that fails on its own motivating case is not being held back by its group-ordering rule.

**Do not re-attempt this without a new idea.** The script is committed so the next attempt starts from a measured baseline rather than the theory.

**Floats are the known ceiling.** `.corpus/truth/wiki_ar.p6.json` scores 0.946 adjacency, and every remaining break is one figure caption whose three lines interleave by `y` with the body text beside them. Geometry cannot separate a float from body prose: the caption is 53px tall against a 1527px block, so `MIN_COLUMN_HEIGHT_RATIO` correctly refuses to call it a column. **Do not loosen that threshold to chase this page** — it would start treating short runs as columns everywhere and cost the pages now at 1.000. The fix is a layout model emitting `caption` regions through the `detector` seam. Truth files use the DocLayNet convention: a caption reads as a contiguous unit, in place, never interleaved.

The wide-table failure ("tabular text cuts into columns and reads down rather than across") **did not reproduce** when measured. `arxiv_gpt3.pdf` p7 is an 8-column table with wide inter-column gaps — the exact shape that should break it — and XY-cut reads it correctly row-wise, 1.000/1.000, where the naive baseline gets 0.832. `MIN_COLUMN_WIDTH_RATIO` 0.25 is doing the work it was added for. Treat this as an unverified risk on denser tables, not a known defect.

### The schema is a published contract

`schema/v{version}.json` is generated from the models and committed. [tests/test_schema_contract.py](tests/test_schema_contract.py) fails if they drift — after an intentional schema change, regenerate with `python -m docyx.schema.contract --write` and decide whether §19 requires a version bump. Older versions are kept as the record of what earlier branches emit: `v1.1` (pre-`PageIssue`), `v1.2` (warnings became structured `PageIssue` records). `v1.3` (`Element.direction` replaced per-element `language`). `v1.4` (`Provenance.modified_by_user` and `original_text`). `v1.5` (`Page.coordinate_system`, `GridPosition.is_header`). `v1.6` is current — `Element.script`, `Document.filename`/`page_count`, and named typography flags. All additive; none add a guess.

`PageIssue` (code/stage/message) carries both errors and warnings, so consumers branch on a stable `code`, never on message text.

### Human edits are provenance, not overwrites

`Element.edit_text()` (§11) is the one way a correction is applied, so the rules hold everywhere the workspace touches:

- **`provenance.source` never changes.** It records the original producer, which is a fact about history. Rewriting it to `manual` would make a corrected OCR line indistinguishable from one a human drew on a blank scan — and that distinction is what an annotation workflow is built on. `modified_by_user` is the flag; `source=manual` is reserved for elements a human *created*, which arrives with the UI.
- **`original_text` is written on the first edit only.** The obvious implementation overwrites it every time, which destroys the machine's value on the second save. Pinned by `test_editing_twice_keeps_the_ORIGINAL_not_the_first_correction`.
- **An edited element becomes `exact` / 1.0.** A person reading the rendered page outranks any extractor, and on a damaged text layer they are the only authority available.

Only text keeps its original. Bbox editing (§10) will set `modified_by_user` without preserving the old geometry — add `original_geometry` when something needs to undo across sessions rather than within one.

### The text-layer gate has two stages

Presence, then quality (§18.3). Quality now covers two independent failures, and `GateResult` carries one warning, so the more severe (garbled text) wins.

**Script coverage is decided by Unicode category, not by a script list**, so neither check is specific to the language it was written against. Verified by parametrised tests in [tests/test_gate.py](tests/test_gate.py): glyph-order scrambling is caught in Bengali, Devanagari, Tamil and Telugu; visual order is caught in Hebrew, Urdu and Persian as well as Arabic; `Direction.of_text` classifies nine scripts correctly.

**Thai is the trap, and it must stay uncovered.** Its pre-base vowels (`เ แ โ ใ ไ`) are category `Lo` rather than `Mn`/`Mc`, so `_combining_mark_order` cannot see them — but they need no detection, because *Unicode stores those vowels before the consonant by design*. `เรียน` beginning with `เ` is correct Thai. An earlier version of the docstring listed Thai as a covered script; "fixing" that by adding `U+0E40..U+0E44` to the orphan test would have flagged every correct Thai page in existence. Pinned by `test_thai_leading_vowels_must_never_be_flagged`.

Detection generalising is not the same as *OCR accuracy* generalising: that is measured for `eng`, `ben` and `ara` only, and needs a document plus a language file per script to extend.

Run `scripts/measure_bidi.py` before trusting any RTL or Indic claim: it groups the corpus by `/Producer`, because **bidi and reordering behaviour is a property of the writing tool, not the script**. It warns on a producer monoculture, which is how the first version of these findings turned out to be Chrome-specific until Word was added.

**`COMBINING_MARK_ORDER`** — the text layer is in glyph order, not logical order. Indic and SE-Asian scripts reorder on display (in বাংলা the vowel sign is typed after its consonant and drawn before it), and a dependent vowel sign can never legitimately begin a word. Measured: **Word-produced Bengali 8.3% word-initial marks, the same language from Chrome 0%**, Arabic and Latin 0%. Every character decodes, so `TEXT_LAYER_SUSPECT`'s replacement-character heuristic is blind to it. This was silently returning scrambled Bengali as `ok` with `confidence 1.0 / exact`.

**The name understates the damage, and "the multiset is intact" — claimed here until it was checked against independent evidence — is false.** Comparing `.corpus/word_bn.pdf` p0's text layer against OCR of its own rendered pixels: `ঘ` GHA, `ঙ` NGA, `ো` VOWEL SIGN O and `়` NUKTA occur **zero times in the text layer** and repeatedly on the page. The earlier claim came from comparing the text layer against itself, which cannot detect a character that was never emitted.

So this is a broken font mapping showing up *as* misordering, and word-initial marks are a symptom rather than the whole defect. The practical consequence: **reordering cannot repair it.** A script-level fix for glyph order — cheap, dependency-free, and the obvious lazy answer — was designed and abandoned on this measurement, because no rearrangement produces a letter the text layer does not contain. OCR is the only recovery path for these documents, which is the strongest argument for it in this codebase.

**`RTL_VISUAL_ORDER`** — right-to-left text stored in *visual* rather than logical order. **Confirmed in the wild on every real Arabic document tested**: four government annual reports (UAE Ministry of Finance, Saudi Ministry of Finance, Saudi Ministry of Commerce, COMCEC), three readable, and all three raise it — from `Adobe PDF Library 15.0` as well as `Microsoft® Word 2010`. Two of them also carry genuinely scanned pages (`NO_TEXT_LAYER`). This is not a quirk of one exporter; it is what Arabic PDFs are. Originally confirmed across Chrome/Skia and Microsoft Word 2021, neither of which emits a non-zero `bidi` level, so this is how PDF works rather than one tool's quirk. Arabic words come back correctly, but bidi-neutral runs (digits, brackets, Latin) are reversed: `.corpus/wiki_ar.pdf` yields `]5[)2021(` where the document reads `(2021)[5]`. Recovering logical order needs the bidi algorithm run backwards, which is ambiguous and lossy — so the page is degraded to `partial` rather than returning scrambled text as `exact` with `confidence 1.0`. Detected by mirrored delimiters on majority-RTL lines only; verified zero false positives across 61 English pages and 8 Bengali. **This means byte-exactness is exact *to PyMuPDF*, not to the document's meaning — a distinction invisible in Latin scripts.**

A page with a text layer that decodes badly — subsetted fonts, no usable ToUnicode — still *passes* (its text is returned) but carries a `TEXT_LAYER_SUSPECT` warning that degrades it to `partial`. The heuristic measures the share of `U+FFFD` and private-use-area characters; `suspect_ratio` is the knob. It is validated against unit cases and a faked reader only — PyMuPDF's writer sanitizes unmappable codepoints on insert, so a real garbled fixture cannot be synthesized in-process.

### Bundle export

`-f bundle -o DIR` writes a tree instead of one blob (§4's output shape):

```
out/
├── document.json                   metadata + a page INDEX, no elements
├── pages/page_001.json             one page with all its elements
├── tables/page_003_table_01.json   one table, cells and all
└── figures/page_004_figure_01.png  cropped from the rendered page
```

`document.json` holds an index — page number, status, element count, issue *codes* — and deliberately not the pages themselves. Writing the whole document there too would put every element in the directory twice, and the copies can disagree after an edit. Tables and figures are extracted copies; the originals stay in their page file.

Figures are cropped by re-rendering the page rather than holding page images through the pipeline: a 150-DPI RGB page is ~6 MB, and keeping one per page for a 200-page report to crop a handful of figures is the wrong trade.

### Markdown export doubles as an evaluation instrument

[docyx/export/markdown.py](docyx/export/markdown.py) reconstructs prose from the page representation. Scrambled output is the fastest available signal that reading order is wrong — `test_two_column_page_reads_down_each_column` was a strict xfail until XY-cut landed and now passes; keep it as the canary. Headings are inferred from font size because layout classification is still a stub; a real layout model's types should take precedence when one lands.

### Real models (optional)

`pip install -r requirements-models.txt` then inject:

```python
from docyx.analysis.detectors.table_transformer import TableTransformerDetector
DocyxPipeline(table_analyzer=TableAnalyzer(detector=TableTransformerDetector()))
```

**Table Transformer is an object detector, not OCR** — it emits row/column/table *boxes* from the page image and never reads a character from pixels (§2.2). Cell text is joined from the native layer by position in `_populate_cell_text`, so it stays `1.0 / exact` — the table model never contributes `ocr` provenance. Verified on a real IRS form: 731 text elements, all `native_pdf`.

The stack is optional by design — core stays at 4 light dependencies (§24). Core throughput is **0.221 s/page median** (`scripts/measure_speed.py`), with stub detectors; the model stack costs far more and has not been timed.

Detectors may declare an `engine` attribute; analyzers report it as `provenance.engine` instead of their own heuristic name. Attributing a model's output to the heuristic it replaced would make §26.11's swappability claim unverifiable.

### OCR (optional, opt-in)

A page with no text layer fails the gate and returns nothing. That is most of what people mean by "a PDF I need to extract", so `OCRAnalyzer` is the fourth analyzer on the same `detector` seam — inject it and gate-failed pages are recognised from the rendered image instead of discarded.

```bash
pip install -r requirements-ocr.txt          # pytesseract + pillow, no torch
python -m docyx scan.pdf --ocr ben           # or ara, or ben+eng
```

Four rules, none of them negotiable:

- **OCR runs only on gate-failed pages.** The native layer is exact by construction; re-reading a page that has one would be a straight downgrade. Pinned by `test_native_text_is_never_re_read_from_pixels`.
- **A recognised page is `partial`, never `ok`.** §24 makes a machine-readable text layer the condition for `ok`, and recognised text does not become one by being good. The gate's `NO_TEXT_LAYER` is demoted from `errors` to an `OCR_TEXT` warning — still true that there's no text layer, no longer true that the page produced nothing, and an `error` on a page with content makes callers throw usable output away.
- **There is no default recogniser.** `ocr_analyzer` defaults to `None`, so the pipeline behaves exactly as it did before OCR existed. Silently degrading `exact` to `inferred` because someone happened to have tesseract on PATH would make the confidence model unreadable.
- **Tesseract, deliberately not PaddleOCR/EasyOCR** — on *runtime* footprint, not download size. It loads no Python ML framework into the process: ~1.8 GB RSS for EasyOCR and ~950 MB for PaddleOCR against tens of MB, plus 8–20 s of cold start. (The first version of this note said "~2 GB of torch", which was wrong twice: PaddleOCR runs on PaddlePaddle, and 2 GB is the CUDA build when both install from a CPU index.) Bengali accuracy was the trade expected to reverse this decision; **measured, it did not** — 0.987 against a clean reference with `tessdata_best`. Swapping engines still costs one file behind the seam if a real scan says otherwise.

`lang` must match the document. `--ocr eng` on a Bengali scan does not fail — it returns confident Latin gibberish, which is worse. `--ocr-min-confidence` (default 0.4) drops low-scoring lines rather than returning them, because page speckle recognised as a one-character "word" lands mid-column and derails the reading order of everything around it.

**Measured** with `scripts/measure_ocr.py`, which destroys a born-digital page's text layer by rendering it to pixels, reads it back with OCR, and scores against the native text it just threw away:

```bash
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_ocr.py .corpus/arxiv_attention.pdf 2 eng
```

| page | lang | native text layer | sequence | char overlap | mean conf |
|---|---|---|---|---|---|
| `arxiv_attention.pdf` p2 | eng | clean | 0.959 | 0.999 | 0.918 |
| `wiki_bn.pdf` p5 | ben | clean | **0.987** | 0.986 | 0.926 |
| `word_bn.pdf` p0 | ben | `COMBINING_MARK_ORDER` | 0.705 | 0.921 | 0.930 |
| `wiki_ar.pdf` p6 | ara | `RTL_VISUAL_ORDER` | 0.140 | 0.928 | 0.829 |

**The two clean-reference rows are the control, and they are what make the other two readable.** Tesseract scores 0.959 and 0.987 where the text layer is trustworthy, so it is not a weak recogniser — which means the low sequence scores on rows 3 and 4 cannot be blamed on it. They are the *reference* being wrong:

- `word_bn.pdf` native reads `বাাংলাদেশ েক্ষিণ এক্ষশযার`; OCR reads `বাংলাদেশ দক্ষিণ এশিয়ার`, which is correct Bengali. Glyph order vs logical order.
- `wiki_ar.pdf` native reads `م180-161(`; OCR reads `(180-161 م)`. Visual order vs logical order.

**So OCR is the only working remedy for the two text-layer defects this project can otherwise only warn about.** `RTL_VISUAL_ORDER` and `COMBINING_MARK_ORDER` are both documented above as unrecoverable from the text layer — recovering them needs the bidi algorithm run backwards, or grapheme reordering, both lossy. Reading the pixels sidesteps the question: the rendered page shows the text as a human reads it. That is a stronger reason for OCR to exist here than scanned-page support, and it was not the reason it was built.

`measure_ocr.py` therefore reports both metrics, and diagnoses a large gap between them as a reordering difference rather than a recognition failure.

English is 0.959 rather than 1.0 because OCR additionally picks up figure labels that the native layer holds as vector art (`Output Probabilities Linear Nx Nx Positional…`), which is extra content, not an error.

#### `--ocr-repair`

The finding above is wired up, behind its own flag:

```bash
python -m docyx word_bn.pdf --ocr ben --ocr-repair
```

On a page whose gate warning is in `REPAIRABLE_CODES`, OCR text becomes `elements` and the native text moves to `diagnostic_elements`. Verified end to end on `.corpus/word_bn.pdf` p0 — native `বাাংলাদেশ েক্ষিণ এক্ষশযার`, repaired `বাংলাদেশ দক্ষিণ এশিয়ার`.

- **Only two codes qualify.** `RTL_VISUAL_ORDER` and `COMBINING_MARK_ORDER` are the cases where the text layer is untrustworthy but the *rendering* is correct. `TEXT_LAYER_SUSPECT` is deliberately excluded: there the glyphs themselves may be undecodable, so OCR might help or might not, and that is not a call to make unattended.
- **The status contract does not move.** Such a page is already `partial` from its warning, so preferring OCR costs nothing that was not already lost.
- **Nothing is discarded.** The native text stays in `diagnostic_elements`, so a consumer that disagrees with this trade still has it.
- **Off by default.** Replacing `exact` text with `inferred` text is never the obvious call.

**Validated on documents nobody constructed for it.** 13 real PDFs pulled from Bangladesh Bank's public circulars (`.corpus/real/`, gitignored), across 6 producers:

| outcome | count | |
|---|---|---|
| processed cleanly | 11 | no crash, no error, 6 different producers |
| `COMBINING_MARK_ORDER` | 1 | `sep272020eefl01_esf_policies.pdf`, Word-produced Bengali policy circular |
| `NO_TEXT_LAYER` | 1 | `81_Annexure-1.pdf`, producer `SECnvtToPDF` — a genuine scan |

The flagged circular is the important one. Its text layer fails **three ways at once** on a single page, which no synthetic fixture would have combined: characters substituted (`বাংলাশে` for `বাংলাদেশ`), one glyph mapped to a wrong multi-character sequence (`ি` arriving as `ক্র`, so `নীতিমালা` becomes `নীক্রতমালা`), and runs replaced by whitespace. Bengali character *counts* barely change — which is why a volume-based check would miss it entirely and why `--ocr-repair` is judged on readability, not on how much text it adds. OCR reads the page correctly.

Two things this corrects. **Word is not the problem** — 7 of the 13 are Word-produced and only one is damaged, so producer alone is not a usable filter. And **scans do occur in the wild** even for someone who owns none: one arrived inside an ordinary circular bundle.

**This is a born-digital feature, not a scanning one.** `word_bn.pdf` is a `PDF 1.7` produced by `Microsoft® Word 2021` — an ordinary emailed document, no scanner involved. The input contract cannot filter this: the damaged file is a valid PDF and passes every format check. Only inspecting the text catches it.

#### Degradation sweep

A flattened page is clean, deskewed and noise-free, so every number above is a **ceiling**. `--sweep` re-runs each page through synthetic scanner damage — skew, JPEG, sensor noise — to find where it breaks:

```bash
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_ocr.py --sweep .corpus/wiki_bn.pdf 5 ben
```

Character overlap, by condition:

| condition | eng | ben | ara |
|---|---|---|---|
| clean | 0.999 | 0.986 | 0.922 |
| skew 1.5° | 0.999 | 0.980 | 0.912 |
| jpeg q40 | 0.999 | 0.984 | 0.924 |
| noise σ12 | 0.999 | 0.986 | 0.937 |
| ordinary office scan | 0.996 | 0.985 | 0.908 |
| **skew 5°** | 0.993 | **0.937** | **0.789** |
| bad photocopy | 0.758 | **0.000** | 0.289 |

Three things this establishes:

- **Skew is the only degradation that matters.** JPEG q40 and noise σ12 cost essentially nothing; 5° of rotation costs Bengali 0.05 and Arabic 0.13. A deskew step is worth more than any amount of denoising.
- **Complex scripts have far less margin.** English survives the bad photocopy at 0.758 while Bengali returns *nothing at all*. Robustness measured on Latin does not transfer, which is the same lesson the English-only corpus taught in phase 4.
- **The last row must fail.** A sweep where every row passes cannot distinguish a robust engine from a harness that is not actually degrading anything — the fixture trap that made this repo's synthetic column tests vacuous. `bad photocopy` is in `DEGRADATIONS` as the harness's own check on itself.

**Not yet acted on:** the pipeline still refuses OCR on any gate-passed page, including these two, so the better text is available only by deliberately discarding the text layer. Making a page with `RTL_VISUAL_ORDER` or `COMBINING_MARK_ORDER` prefer OCR would be a real improvement and a real complication — it needs a rule for which source wins, and both would be `partial` either way.

### Page status and failure isolation

Every page returns a `Page`, whatever happened to it. `_safe_page` wraps the whole
per-page path, so one corrupt page costs that page and not the document — a
200-page report with one bad page returns 199 good ones plus a `FAILED` page
carrying the exception. Codes: `NO_TEXT_LAYER` (gate), `EXTRACTION_FAILED` (text
layer blew up on a gate-passed page), `PAGE_UNREADABLE` (anything else, including
an out-of-range page index).

`source_type` is derived, never defaulted: `born_digital`, `scanned` (no text but
raster content present), `empty` (neither), `unknown` (page unreadable). It sat at
a hardcoded `born_digital` for three phases, which made it wrong on precisely the
pages it exists to mark.

**A zero-page PDF is rejected too**, in the same place. One of the real Arabic reports is a valid, unencrypted 14 MB `PDF 1.6` whose page tree resolves to nothing. Left alone, `process()` returned a `Document` with zero pages and no error, and the CLI exited **0** — because "every page produced a valid result" is vacuously true of no pages. A 14 MB file that produced nothing was being reported as a success.

**Non-PDF input is rejected at the boundary** in `PDFRenderer`. PyMuPDF also opens
XPS, EPUB, CBZ and Office documents, so `fitz.open()` succeeding is not evidence of
a PDF — a `.docx` went through the whole pipeline reported as `ok` before this check
existed.

### Known gaps

- **Scanned PDFs need `--ocr`**; without the flag a scanned page still fails, by design. Measured on flattened born-digital pages only — **no real scan has ever been through this pipeline**, so skew, JPEG artefacts and show-through are entirely unrepresented in every number above.
- **Layout classification is a stub**: no `layout_region` is ever produced without an injected detector, and none ships. `TableAnalyzer` is the same seam and *does* have a working detector, so the pattern is proven rather than speculative.
- `figure` detection is a contour heuristic. Dense text used to be misreported as figures (434 of them in a 114-page RFC); a component-density filter now rejects candidates that fragment like text. Inject a real figure head via `detector` when precision matters.
- `visual_inference` provenance is still unused, and has no planned producer.

## Workspace (phase 5, first slice)

```bash
PYTHONPATH=. .venv/Scripts/python.exe -m docyx.workspace file.pdf [--layout] [--tables] [--ocr ben]
```

Renders each page with its extracted elements drawn over it; click a box to see its JSON. Boxes are coloured by `confidence.type` — green `exact`, blue `detected`, amber `inferred`, purple edited — so a page's trustworthiness is visible before reading anything.

**This exists as a feedback loop, not a feature.** The last code review found 12 issues, three of which were regressions that 219 passing tests missed — including `--layout` taking a page from 3 headings to zero — because no test ever rendered the output the way a user sees it.

`http.server` from the stdlib, deliberately: one image, one JSON blob and one HTML file do not justify a fifth core dependency. The routes are thin so FastAPI can replace it when uploads, auth or concurrency arrive.

Editing is not wired up yet. The schema is ready for it — `edit_text()`, `modified_by_user`, `original_text`, `source: manual`.

## Planning docs

`.planning/` (GSD workflow: `ROADMAP.md`, `STATE.md`, per-phase dirs) tracks the 6-phase roadmap; phases 1–4 are complete. Phase 5 is the workspace UI — but the CLI, listed under phase 5, shipped early because being fast and light buys nothing while the tool is import-only. The two root markdown plans are the authoritative spec and cross-reference each other by section number — read together, neither is self-contained:

- `universal_page_document_metadata_extraction_plan_v6.md` — architecture, pipeline, schema
- `universal_page_metadata_extraction_tool_uiux_plan_v3.md` — the phase-5 workspace UI

`AGENTS.md` is **stale** — it claims the repo is planning-only with no code. Its v1 scope constraints and architecture notes are still accurate; ignore the "no code yet" framing.

## Licensing

**AGPL-3.0** (`LICENSE`), because PyMuPDF is AGPL-or-commercial and the project's stated differentiator is open-source self-hosted operation. See [LICENSING.md](LICENSING.md) for the dependency inventory and what the choice forecloses — notably a proprietary hosted API or closed enterprise deployment, both of which appear in §24's commercialization sketch.

PyMuPDF is confined to [docyx/pdf/](docyx/pdf/); everything outside depends on the protocols in [docyx/pdf/protocols.py](docyx/pdf/protocols.py), and `test_pymupdf_stays_inside_docyx_pdf` fails the build if that stops being true. It had already leaked: the pipeline held a raw `fitz.Document` as `renderer.doc` and passed it to two collaborators, so the backend type was in the hands of a module meant to know only the protocols. `PDFRenderer.text_extractor()` and `.text_document()` close it. **Keep it that way** — that containment is what makes the licence decision reversible for the cost of one package. Any injected layout/table model brings its own licence; that audit is still outstanding.

## v1 scope limits (non-negotiable)

PDF-only input, reject non-PDF at upload. No GPU, no bundled model weights. **A machine-readable text layer is still the condition for `ok`** — OCR is opt-in and its output is `partial` / `inferred`, which keeps that limit intact rather than repealing it.

"No OCR" was a v1 limit until the owner lifted it; the resource constraint behind it was not lifted, which is why the recogniser is a 5 MB binary behind an optional dependency and not a torch stack.
