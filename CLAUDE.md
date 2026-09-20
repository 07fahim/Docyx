# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

No build step, no packaging (`pyproject.toml` does not exist). Run everything through the venv interpreter:

```bash
PYTHONPATH=. .venv/Scripts/python.exe -m docyx file.pdf -o out.json   # CLI
PYTHONPATH=. .venv/Scripts/python.exe -m docyx *.pdf -o results/ -f markdown
PYTHONPATH=. .venv/Scripts/python.exe -m docyx scan.pdf --ocr ben   # optional OCR, see below
PYTHONPATH=. .venv/Scripts/python.exe -m docyx paper.pdf --layout --tables -f bundle -o out/
PYTHONPATH=. .venv/Scripts/python.exe -m docyx book.pdf --layout -f blocks -o out/  # image + annotation pairs
.venv/Scripts/python.exe -m pytest -q            # full suite (329 tests, ~43s)
.venv/Scripts/python.exe -m docyx.schema.contract --write   # regenerate schema/v1.8.json after a schema change
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
- **Provenance sources active:** `native_pdf`, `layout_model`, `table_model`, `geometry_inference`, `manual`, and `ocr` (only when an OCR detector is injected). `visual_inference` stays reserved and unemitted — it means typography estimated from pixels, and the only available estimator was measured and rejected (**Typography from pixels**).
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

Graded against hand-labelled ground truth in `.corpus/truth/`, not against another tool.

**One directory per scorer**, and it is load-bearing: each harness globs its own directory, so a truth file for one must never sit in another's. `.corpus/truth/*.json` is reading order; `truth/types/`, `truth/tables/`, `truth/ocr/` are the rest. The OCR reference spent a session in the reading-order directory and crashed that scorer on every run with a bare `KeyError` — which reads as "the harness is broken" and left the tau quoted below unreproducible. `load_truth` now names the problem instead.

```bash
PYTHONPATH=. .venv/Scripts/python.exe scripts/dump_lines.py .corpus/x.pdf 3   # labelling worksheet
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_reading_order.py        # tau + adjacency
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_types.py               # semantic roles, per class
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_tables.py              # table structure (needs models)
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_table_regions.py       # does it fire ONLY on tables
PYTHONPATH=. .venv/Scripts/python.exe scripts/sample_table_detections.py draw 40   # precision, with an interval
PYTHONPATH=. .venv/Scripts/python.exe scripts/compare_tools.py               # vs Docling
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_speed.py               # wall clock
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_bidi.py                # RTL per producer
PYTHONPATH=. .venv/Scripts/python.exe scripts/find_scanned_pages.py          # real scans on disk, by producer
PYTHONPATH=. .venv/Scripts/python.exe scripts/fetch_scans.py ben 8         # grow the scan corpus, verified
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

**`script`, not `language` (§7).** The spec asks for `language`. The original reasoning was that a PDF carries none, so `"en"` could only come from statistical detection — a guess, which cannot sit in a schema where every value declares its own reliability.

**That premise was imprecise, and the conclusion came out stronger.** Some PDFs *do* declare `/Lang`. Four in the corpus do, and **two are wrong**:

| file | declares | actually |
|---|---|---|
| `wiki_ar.pdf` | `ar` | Arabic ✓ |
| `wiki_bn.pdf` | `bn` | Bengali ✓ |
| `word_ar.pdf` | `en-US` | **Arabic** |
| `word_bn.pdf` | `en-US` | **Bengali** |

`/Lang` records the authoring tool's UI locale, not the document's language — and it fails on exactly the Word-produced non-Latin files this project exists for, the same two carrying `COMBINING_MARK_ORDER` and `RTL_VISUAL_ORDER`. So do not read it: a declared-but-wrong value is worse than an absent one, because it looks authoritative. Script is derived from the characters and cannot lie about them. Script *is* in the characters: Unicode names U+0995 `BENGALI LETTER KA`, so the first word of the codepoint's name is the answer, with no table and no model. It cannot tell English from French and does not pretend to; it tells Bengali from Arabic from Latin, which is what these documents turn on. Verified: `wiki_ar` p6 all `arabic`, `word_bn` p0 all `bengali`, `arxiv_attention` p0 all `latin`.

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

`schema/v{version}.json` is generated from the models and committed. [tests/test_schema_contract.py](tests/test_schema_contract.py) fails if they drift — after an intentional schema change, regenerate with `python -m docyx.schema.contract --write` and decide whether §19 requires a version bump. Older versions are kept as the record of what earlier branches emit: `v1.1` (pre-`PageIssue`), `v1.2` (warnings became structured `PageIssue` records). `v1.3` (`Element.direction` replaced per-element `language`). `v1.4` (`Provenance.modified_by_user` and `original_text`). `v1.5` (`Page.coordinate_system`, `GridPosition.is_header`). `v1.6` (`Element.script`, `Document.filename`/`page_count`, named typography flags). `v1.7` (`Provenance.original_geometry`, which bbox editing needs to be non-destructive). `v1.8` (`Provenance.original_type`, the same for the block's category). `v1.9` is current — `Document.issues`, for facts about the document rather than any one page. All additive; none add a guess.

`PageIssue` (code/stage/message) carries both errors and warnings, so consumers branch on a stable `code`, never on message text.

### Human edits are provenance, not overwrites

`Element.edit_text()` (§11) is the one way a correction is applied, so the rules hold everywhere the workspace touches:

- **`provenance.source` never changes.** It records the original producer, which is a fact about history. Rewriting it to `manual` would make a corrected OCR line indistinguishable from one a human drew on a blank scan — and that distinction is what an annotation workflow is built on. `modified_by_user` is the flag; `source=manual` is reserved for elements a human *created*, which arrives with the UI.
- **`original_text` is written on the first edit only.** The obvious implementation overwrites it every time, which destroys the machine's value on the second save. Pinned by `test_editing_twice_keeps_the_ORIGINAL_not_the_first_correction`.
- **An edited element becomes `exact` / 1.0.** A person reading the rendered page outranks any extractor, and on a damaged text layer they are the only authority available.

`edit_geometry()` (§10) and `edit_type()` (§8) are the twins. **Three claims, three separate guards** — `original_text`, `original_geometry`, `original_type`. Gating them on the shared `modified_by_user` flag means whichever edit came second records nothing: correct the text of a block you already recategorised, and the machine's own category is silently gone. Pinned by `test_a_type_edit_never_overwrites_the_other_originals`.

**`edit_type` is the one claim with no automatic answer.** Without a layout model every extracted line is `text`, so a paper's title, its section headers and its paragraphs are indistinguishable — and the `blocks` export labels all of them `Text`. `ASSIGNABLE_TYPES` (models.py) is the closed vocabulary, deliberately the DocLayNet 11 and nothing more: a label outside the export target's categories is a label no consumer can use and no model will ever predict. Containers are excluded — nobody reassigns `text_region` or `table_cell` one block at a time.

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

### Block-annotation export

`-f blocks -o DIR` writes [docyx/export/blocks.py](docyx/export/blocks.py) — page images paired with flat block annotations, the shape page-annotation tools read when building OCR training data. One entry per block: a category, its text, its box, nothing else. Flat, with matching stems, because that pairing is how such tools associate the two and they generally read only files directly inside the chosen folder.

**Why this format and not another.** The usual bottleneck in that workflow is *position* — the category and the words are typically transcribed already, and what is missing is where on the image each block sits. Producing positions is exactly what Docyx does, so this turns a measuring pass into a checking one.

- **Lossy by design.** The entry is closed at `{category, text, bbox}`, so confidence, provenance, script, typography and reading order are all dropped. Use `json` or `bundle` when any of that matters.
- **Coordinates are clamped, never shifted.** `bbox` is `[left, top, right, bottom]` in image pixels with origin 1, and Docyx's 150-DPI reference pixels *are* the rendered PNG's pixels, so there is no scaling. Real pages need the clamp: `wiki_ar.pdf` p6 has a justified RTL line at `x = -11.9`. Adding one to every coordinate would satisfy the origin rule and move every box off its words.
- **`--layout` changes the granularity, and should.** Blocks here are paragraphs; Docyx extracts lines (§5). With a layout model the regions *are* the blocks and the lines inside supply the words — `arxiv_attention` p2 exports 10 blocks. Without one, lines are the best available answer: `wiki_ar` p6 exports 57. More entries than a person would draw, every one correct.
- **A line in no region is still exported.** Dropping it produces the exact defect a review pass hunts for — a block on the page with no box at all.
- **`formula` is dropped and reported, not relabelled.** DocLayNet has 11 classes and this format carries 10. Exporting a formula as `Text` would poison the very labels the export exists to produce; an unannotated block is something a reviewer catches, a mislabelled one is not.
- **`validate()` mirrors the consumer-side checks and runs before writing.** Claiming compatibility is cheap; refusing to write a file an annotation tool would reject is what makes the claim testable. Unknown category, wrong field set, inside-out or upside-down box, anything below 1, anything past the image edge.
- **The category is only as good as `type`, which is why it is editable.** Without a layout model every element is `text`, so a title, a section header and a paragraph all export as `Text`. `edit_type()` and the workspace's type control are what make this field mean anything — see **Human edits are provenance**.

### Heading suggestions from typography

[docyx/analysis/headings.py](docyx/analysis/headings.py) proposes `title` and `section_header` from font size. `markdown.py` already ranked sizes to render `#` and then discarded the ranking; this offers it as a suggestion instead. No weights, no download, no GPU.

**It proposes and never writes**, which is not squeamishness — it is forced. Mutating `type` during extraction would require a lie about confidence: downgrading the element to `detected` claims the *text* is uncertain when it was read exactly, while leaving it `exact` shows a guessed category in the trust colour. `Confidence` describes the whole element, but an element now carries three claims — text, geometry, type — that differ in how sure they are. Until those are separated, "a suggestion a person accepts" is the honest shape: the workspace's `Suggest` button applies each one through `edit_type`, so the record says a human decided, which is true.

**It only fires on prose.** The share of lines at the modal font size separates the corpus cleanly, with nothing near the boundary:

| page | modal share | proposals |
|---|---|---|
| `rfc2616` p12 | 96% | 2, both correct |
| `wiki_ar` p6 | 91% | 2, both correct |
| `wiki_bn` p5 / `nasa_budget` p88 | 89% | correct |
| `arxiv_attention` p0 | 46% | 6, one wrong |
| `irs_f1040` p0 | 71% | 7, unreliable |
| `irs_fw9` p0 | 44% | **15 of 133 lines, mostly wrong** |

A form has no dominant body size, so "bigger than body" stops meaning "heading" and starts meaning "one of the other six sizes on this page". Below `MIN_BODY_SHARE` (0.8) it proposes nothing — the same rule the alignment code follows, where an absent value beats a wrong one.

**A page has at most one title**, and **a document has one title, on page one**. Two lines sharing the largest size are two section headings, which is exactly what `wiki_ar.pdf` p6 is; calling both `title` was the first version's bug. The page rule is read off the element id (`page89_b1_l0`), and it is what stops `nasa_budget.pdf` p88 proposing a `title` on page 88 of a budget.

The worry that drove the measurement — that Arabic and Bengali mark hierarchy by ornament rather than size — **did not hold**. Arabic scored 2/2 and Bengali 1/1. Forms are the failure mode, not scripts.

**Everything above is precision, and precision alone was hiding the real defect** — see the next section.

### The type scorer

`analysis/headings.py` and the workspace's type control both shipped with nothing that could say whether their output is right, which is the exact trap the reading-order harness exists to avoid. `scripts/measure_types.py` closes it.

```bash
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_types.py
```

Truth lives in `.corpus/truth/types/` and **shares its line inventory and checksum with the reading-order truth**, so labelling a page costs one `dump_lines.py` worksheet rather than two, and a change to extraction invalidates both together. A file records only the lines that are *not* plain text, because "everything is text" is precisely the baseline being measured.

**Accuracy is not reported, deliberately.** 78% of the labelled lines are `text`, so answering `text` to everything — exactly what the pipeline does with no layout model — scores 0.78 and has found nothing. Per-class precision and recall against that baseline is the only reading that separates the two. 276 lines, 5 pages, 3 scripts:

| class | n | bare P / R / F1 | suggested P / R / F1 |
|---|---|---|---|
| `title` | 2 | – / 0.00 | – / **0.00** |
| `section_header` | 14 | – / 0.00 | 0.86 / 0.43 / **0.57** |
| `caption` | 6 | – / 0.00 | – / 0.00 |
| `page_header` | 5 | – / 0.00 | – / 0.00 |
| `page_footer` | 4 | – / 0.00 | – / 0.00 |
| `table` | 31 | – / 0.00 | – / 0.00 |
| `text` | 214 | 0.78 / 1.00 / 0.87 | 0.80 / 1.00 / 0.89 |
| **macro F1** | | **0.125** | **0.208** |

**It found two defects on its first run**, both invisible to the table above it:

- **`title` on an interior page.** `nasa_budget` p88 sets its program name larger than anything else and was proposed as a `title`. Fixed by the page-one rule; pinned by `test_the_biggest_line_on_an_interior_page_is_not_a_title`. That single change took `section_header` F1 from 0.33 to 0.57, because three pages were spending their largest line on a `title` that was really a section heading.
- **The suggester's recall is 0.43, and nothing had ever measured it.** The modal-share table reports how many proposals were *correct* and never how many headings were *missed*. On the labelled set it finds 6 of 14.

**`title` recall is 0.00, and that is `MIN_BODY_SHARE` working as designed.** `arxiv_bert.pdf` p0 proposes nothing at all: a title page sets its abstract in a different size from its body, so the modal share falls under 0.8 — the same gate that stops `irs_fw9` producing 15 wrong proposals. Title pages and forms look alike by this measure. **Do not loosen the threshold to chase the two title lines**; it is the form disaster that comes back, and the numbers for it are in the modal-share table above.

`caption`, `page_header`, `page_footer` and `table` are all 0.00 under both predictors and there is no heuristic in the codebase that could move them. They are in the truth so that a future layout model has something to be graded against, and so that a heuristic which buys heading recall by mislabelling a caption is caught doing it.

**5 pages is an instrument, not a benchmark** — the same caveat the reading-order harness carries, and here it is tighter: 2 title lines cannot support a claim about titles.

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

**`HF_HOME` must not be quoted.** A value of `"D:\caches\huggingface"` including the literal quotes makes every detector raise `OSError: [Errno 22]` deep inside `huggingface_hub`, which `_safely` turns into a `STAGE_FAILED` warning and zero tables — so the stack looks like it silently detects nothing rather than like it is misconfigured. Cost an hour once; check this first if a detector yields nothing.

### The table structure scorer

`TableAnalyzer` shipped a working Table Transformer through the `detector` seam and nothing could say whether its grid was right — the last capability in the repo still in the trap the reading-order harness exists to close.

```bash
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_tables.py
```

**A cell is a set of text lines, not a string.** Cell text is joined from native lines that fall inside the cell box, so "did every line land in the right cell" is what decides whether a table is right — and it is what a text comparison cannot measure honestly, because a multi-line cell differs from its reference by spacing and join order long before it differs in content. Truth records line *indices* from `dump_lines.py` and shares its checksum, so an extraction change invalidates these and the reading-order files together.

Two numbers: `cell` (a predicted cell counts only when its line set exactly equals a truth cell's) and `adj` (the ICDAR-style adjacency metric — each truth cell's nearest occupied neighbour right and below). Quote both. `adj` relations are keyed on *content*, not grid coordinates, so a missed header row is not punished twice by renumbering everything under it.

| table | shape | cov | cell F1 | adj F1 | naive adj F1 |
|---|---|---|---|---|---|
| `arxiv_attention` p5 | 5×4 | 0.76 | 0.865 | **0.857** | 0.852 |
| `arxiv_gpt3` p7 | 9×8 | 0.89 | 0.941 | 0.937 | **1.000** |
| `nasa_budget` p88 | 2×5 | 0.94 | 0.640 | 0.400 | **0.444** |
| mean | | | 0.815 | **0.731** | **0.766** |

**The headline: the model does not beat naive geometry.** The baseline clusters line positions by x and y — no model, no weights, no download — and matches or beats Table Transformer on all three tables. Read that with its caveat, which is why `cov` is in the table: the baseline is *handed* the table's line set and the model has to find it. `cov` is the share of labelled table lines the model put in any cell, and it separates the two failures:

- **`arxiv_gpt3` p7 is a region failure, not a structure failure.** Cell precision is 1.000 — every cell it reported is exactly right. Its single error is that the header row is not inside the detected table, which is precisely the information the baseline gets free. Do not read its 0.937 as the model being worse at structure.
- **`nasa_budget` p88 is a real structure failure.** `cov` 0.94, yet cell precision 0.533: the model emitted **two identical header rows**, and clipped the last line off a ten-line cell. Nothing about the region explains that.
- **Losing the header row happens on all three** (`cov` 0.76 / 0.89 / 0.94). It is the one defect worth fixing.

**The baseline must be able to win, or the comparison proves nothing.** The first version used "one cell per line, one column", which scored 0.005 and could never have flagged anything — the same vacuous-fixture trap that `bad photocopy` exists to prevent in the OCR sweep. `arxiv_attention` p5 is where the model does edge ahead (0.857 vs 0.852), so the flag demonstrably both fires and doesn't.

### Tables with no model

[docyx/analysis/table_geometry.py](docyx/analysis/table_geometry.py) recovers tables from **text-line geometry**, behind `--tables` / `DocyxPipeline(table_geometry=True)`. No weights, no download, 7 ms/page.

It exists because the scorer above said the model was not buying structure. Scored the honest way — finding its own region, no truth handed to it — it **beats Table Transformer**:

| | adj F1 | |
|---|---|---|
| `table-geometry-v1` | **0.749** | model-free, finds its own region |
| Table Transformer | 0.731 | torch + 110M weights |
| naive (handed the table's lines) | 0.766 | an upper bound, not a competitor |

On `arxiv_gpt3` p7 it scores **1.000 with all 72 cells**, where the model gets 64 because it drops the header row.

**The seam still wins.** An injected model had the page image and gets the first say; the geometry pass runs only when no `table` element exists. Pinned by `test_a_table_yields_to_an_injected_model`.

**Finding the region is the whole problem, and precision is graded first** (`scripts/measure_table_regions.py`): a missed table costs a feature, while a two-column page called a table silently reorders a page that was correct. Currently 1.00 coverage on the three labelled tables and **zero false tables across nine pages that have none**.

**The discriminator is width *variation*, not width.** `MAX_FILL` looked sufficient until a two-column abstract measured 0.84 against a real table's 0.80 — inseparable. Prose lines all run to the same measure (CV 0.32); table cells do not (0.52–0.75); equations and diagram labels vary more than either (0.98, 1.12). A table is bounded on both sides.

**It is off by default, and it is now measured how far off it is.** `measure_table_regions.py` grades hand-picked adversarial pages, which is a bug-finding tool rather than a measurement: the pages are chosen by whoever already knows where the bugs are, so "nothing broke on my nine pages" could never converge. `scripts/sample_table_detections.py` measures the quantity that actually decides the default — **what share of detections on an unseen page are real tables** — by running all 1325 corpus pages, drawing a seeded random sample of the 500 detections, and reporting a Wilson interval.

The bar was set at 0.95 *before* adjudication, and **the lower bound decides**, not the point estimate: 20 clean samples read 1.000 with a bound near 0.84.

| draw | seed | precision | 95% CI | detections |
|---|---|---|---|---|
| before the furniture fix | 20260920 | 0.650 | [0.495, 0.779] | 500 |
| after | 777 | 0.850 | [0.709, 0.929] | 434 |
| after | 31415 | **0.700** | [0.546, 0.819] | 434 |
| **after, both draws pooled** | | **0.766** | **[0.660, 0.847]** | 434 |

**Every row is an independent random draw**, so row 2 measures the fix rather than re-checking the pages it targeted. Worksheets for all three are kept in `.corpus/truth/table_regions/`.

The first draw's 14 false positives fell into six families, **none of which appeared in the nine adversarial pages** — the discovery curve had not flattened, it had barely started. Five of the fourteen began with a running head (`RFC 2616 | HTTP/1.1 | June, 1999`), one root cause worth fixing: three short aligned cells at the top of every page are a perfect false anchor, establishing three columns and then pulling the prose beneath into the box. `_strip_furniture` removes it, which is correct regardless of detection — a running head belongs to no table.

**The third draw is the reason to quote the pooled row and not row 2.** Seeds 777 and 31415 sample the same 434 detections and read 0.850 and 0.700 — a 0.15 spread from sampling alone. Reporting the first of those as "the measurement after the fix" would have overstated it by more than the fix itself was worth. **n=40 is too small to quote singly**; pooled over 77 distinct detections the interval finally narrows to ±0.09.

**0.660 does not clear 0.95, so tables stay off by default**, and the gap is wider than one draw suggested. The surviving families, pooled: **indexes** (3), **acronym glossaries** (3), **prose mistaken for columns** (3), **bibliographies** (2), **form instruction prose** (2), plus one each of infobox-across-the-gutter, code list, example figure and RFC front matter. Indexes and glossaries are now the two largest, and they share a shape — an unruled two-column term/definition list is geometrically indistinguishable from a table, which is the point at which geometry alone has run out.

**Adjudication is checked against itself.** Three detections were drawn by both seeds, and both rounds labelled all three identically — so the verdicts track a rule rather than the result. Each draw's worksheet records the rule and names the calls a second adjudicator could reasonably flip; the four decided by looking at the rendered page rather than the cell text are named too, because "I read the cells" and "I looked at the page" are not the same evidence.

**3 tables is an instrument, not a benchmark**, and one of the three is deliberately the hardest page in the corpus. The metric itself is unit-tested without weights in [tests/test_table_transformer.py](tests/test_table_transformer.py) — a scorer nobody can check is worth no more than the score it prints.

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

**Every flag that can be wrong is validated at parse time, and the reason is one recurring defect: a bad argument surfacing as a bad *document*.** Four of them shared that shape, and each one was found by running the flag rather than reading it:

| flag | what it did | what it says now |
|---|---|---|
| `--ocr ben` (no `ben.traineddata`) | `1 failed [NO_TEXT_LAYER]` | names the missing language **and what is installed** |
| `--ocr scan.pdf` | *"the following arguments are required: pdfs"* — blames the argument you did supply | "looks like a file, not a language code" |
| `--ocr-min-confidence 50` | `1 failed [NO_TEXT_LAYER]` — reads as a percentage, drops every line | "this is a probability, not a percentage" |
| `--port 8000` when taken | **hung silently** while the first server kept answering | "port 8000 is already serving" |
| `-o a/b/c.json` (no `a/b`) | bare `FileNotFoundError` out of `write_text()` | creates it, as batch mode already did |

The last one is the sharpest: `ThreadingHTTPServer` sets `SO_REUSEADDR`, which **on Windows lets the second bind succeed**. The second process then sat in `serve_forever` answering nothing while the first kept the connections — so starting the workspace twice looked like it worked and showed the other document. `_port_is_taken` connects rather than binds, because a socket merely in `TIME_WAIT` does not accept and an immediate restart must keep working.

The workspace CLI also now rejects a missing path and an out-of-range port itself, instead of letting PyMuPDF's `FileNotFoundError` and `socket.bind`'s `OverflowError` reach the terminal as tracebacks.

**`--ocr` is validated at parse time, and the reason is a defect that looked like something else entirely.** `--ocr ben` without `ben.traineddata` installed raised per page; `_safely` caught it as `STAGE_FAILED`, so the summary printed `1 failed` and the honest reading was *"this scan is unreadable"* rather than *"install a 5 MB data file"*. Two guards now:

- The language must be in `detector.languages()`, checked before any page runs, and **the error names what *is* installed** — that is the difference between a dead end and a fix.
- A value ending `.pdf` or containing a path separator is rejected by `type=ocr_language`. `docyx --ocr scan.pdf` otherwise swallows the PDF as the language and argparse then reports *"the following arguments are required: pdfs"*, blaming the one argument you did supply.

Both are in `docyx.workspace`'s CLI too. This is the same failure shape as the `HF_HOME` trap above: a misconfiguration that a broad `except` turns into "found nothing".

**Confidence cannot detect a wrong language either — measured, and it is the reason the warning above is the *only* guard.** The obvious next step is to flag a page whose OCR confidence is low and say "maybe you named the wrong language". On the real Bangladesh Bank scan, mean OCR confidence is **0.870 read as `eng`** (CER 0.649, zero Bengali recovered) against **0.916 read as `ben+eng`** (CER 0.075). A 0.046 gap between garbage and correct. Tesseract is *confidently* wrong, which is the whole reason this failure mode is dangerous.

What does move is how many lines survive `--ocr-min-confidence`: 19 with `eng` against 33 with `ben+eng`, and on `wiki_ar.pdf` p6 five against fifty-eight. But with no expected line count there is no threshold to put on it, so it stays an observation.

*(A first pass at this measured the mean over `page.elements`, which also holds the visual detector's output, and produced an apparent 0.690/0.769 split that looked shippable. It was an artefact. Measure OCR confidence over the OCR elements.)*

**`--ocr` will not guess the language, and that was measured rather than assumed.** Tesseract's OSD detects script from the page image and gets 5 of 6 corpus pages right — `wiki_bn` Bengali at 69.5, `wiki_ar` Arabic at 45.2, Latin pages correctly. It fails on **the one page that matters**: `.corpus/real/81_Annexure-1.pdf`, the actual Bangladesh Bank scan, comes back `Latin` at confidence 0.88. A born-digital page flattened to pixels is clean and single-script; a real mixed Bengali/English scan is neither, and that is exactly the input auto-detection exists to serve. So the flag stays explicit.

**Measured** with `scripts/measure_ocr.py`, which destroys a born-digital page's text layer by rendering it to pixels, reads it back with OCR, and scores against the native text it just threw away:

```bash
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_ocr.py .corpus/arxiv_attention.pdf 2 eng
```

| page | lang | native text layer | sequence | char overlap | mean conf |
|---|---|---|---|---|---|
| `arxiv_attention.pdf` p2 | eng | clean | 0.959 | 0.999 | 0.918 |
| `wiki_bn.pdf` p5 | ben | clean | **0.980** | 0.974 | 0.926 |
| `word_bn.pdf` p0 | ben | `COMBINING_MARK_ORDER` | 0.775 | 0.914 | 0.929 |
| `wiki_ar.pdf` p6 | ara | `RTL_VISUAL_ORDER` | 0.646 | 0.928 | 0.829 |

**The two clean-reference rows are the control, and they are what make the other two readable.** Tesseract scores 0.959 and 0.980 where the text layer is trustworthy, so it is not a weak recogniser — which means the low sequence scores on rows 3 and 4 cannot be blamed on it. They are the *reference* being wrong:

- `word_bn.pdf` native reads `বাাংলাদেশ েক্ষিণ এক্ষশযার`; OCR reads `বাংলাদেশ দক্ষিণ এশিয়ার`, which is correct Bengali. Glyph order vs logical order.
- `wiki_ar.pdf` native reads `م180-161(`; OCR reads `(180-161 م)`. Visual order vs logical order.

**So OCR is the only working remedy for the two text-layer defects this project can otherwise only warn about.** `RTL_VISUAL_ORDER` and `COMBINING_MARK_ORDER` are both documented above as unrecoverable from the text layer — recovering them needs the bidi algorithm run backwards, or grapheme reordering, both lossy. Reading the pixels sidesteps the question: the rendered page shows the text as a human reads it. That is a stronger reason for OCR to exist here than scanned-page support, and it was not the reason it was built.

`measure_ocr.py` therefore reports both metrics, and diagnoses a large gap between them as a reordering difference rather than a recognition failure.

English is 0.959 rather than 1.0 because OCR additionally picks up figure labels that the native layer holds as vector art (`Output Probabilities Linear Nx Nx Positional…`), which is extra content, not an error.

#### `autojunk` — a measurement bug that only hurt non-Latin scripts

**Every sequence number above was wrong, and wrong by an amount that depended on the script.** `difflib.SequenceMatcher` defaults to `autojunk=True`: past 200 elements it treats any character occupying more than 1% of positions as junk and refuses to anchor matches on it. That heuristic is tuned for source code, where a handful of punctuation characters dominate. An abugida or an abjad is the opposite case — dense Bengali reuses `্ া র ে` and Arabic its two dozen letters in far more than 1% of positions, so **the most common letters on the page become unmatchable** and the alignment shatters into phantom `replace` blocks that are really the same text.

Measured by scoring the *same* recognised output both ways, so nothing but the flag differs:

| page | script | chars | ON | OFF | |
|---|---|---|---|---|---|
| `arxiv_attention` p2 | latin | 1826 | 0.959 | 0.959 | **+0.000** |
| `real_81_annexure` p0 | bengali+latin | 994 | 0.954 | 0.956 | +0.002 |
| `wiki_bn` p5 | bengali | 2132 | 0.978 | 0.980 | +0.002 |
| `real_mou_signature` p13 | latin | 488 | 0.677 | 0.764 | +0.087 |
| `word_bn` p0 | bengali | 900 | 0.700 | 0.775 | +0.075 |
| `scan_ben_vol24` p10 | bengali | 1688 | 0.788 | 0.950 | +0.162 |
| `wiki_ar` p6 | arabic | 3912 | 0.140 | **0.646** | **+0.506** |

**Latin is the one row that does not move at all.** A harness validated on English would never have shown this, which is the same lesson the English-only corpus taught in phase 4, arriving this time in the *instrument* rather than the subject.

Two documented findings were overstated by it. `wiki_ar.pdf` p6's **0.140 was mostly an artefact** — the real figure is 0.646. The conclusion it supported still holds, because 0.928 overlap against 0.646 sequence is still a large gap and still says visual order, but the magnitude was not evidence of anything. And the first real Bengali *prose* scan reads **CER 0.052 where the broken alignment said 0.214**, a 4x overstatement of the error rate on precisely the script this project exists for.

**The damage is worst where there is already some error.** Two nearly perfect strings match in long contiguous runs and autojunk has little to spoil (`wiki_bn` p5, +0.002); scatter small errors through them and it can no longer re-anchor between them (`scan_ben_vol24`, +0.162). So it inflates hard pages and leaves easy ones alone — the opposite of a constant offset, and undetectable by spot-checking a good result.

`matcher()` in [measure_ocr.py](scripts/measure_ocr.py) is now the single place the flag is set, and all three call sites route through it. `compare_tools.py` already passed `autojunk=False`; this harness never did. **Pass it explicitly in any new comparison** — the default is wrong for every script this project cares about.

#### A real scan, finally

Every row above is a born-digital page flattened to pixels — a ceiling. `--truth FILE` grades an actual scanner's output against a hand-typed reference, because the page has **no text layer to grade against**:

```bash
PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_ocr.py \
    --truth .corpus/truth/ocr/real_81_annexure.p0.json .corpus/real/81_Annexure-1.pdf 0 ben+eng
```

`.corpus/real/81_Annexure-1.pdf` — a Bangladesh Bank agent-banking return, producer `SECnvtToPDF`, mixed Bengali and English, a ruled table, a handwritten signature. Derived `source_type: scanned`, `status: partial` / `OCR_TEXT`, exactly as the contract says.

| | |
|---|---|
| **CER** | **0.073** — 0.028 excluding the form's dotted leaders |
| sequence similarity | 0.956 |
| character overlap | 0.916 |
| distinct Bengali glyphs recovered | **44 / 44** |
| mean confidence | 0.898 |

**Where the 77 edited characters actually are**, which is the part worth keeping:

| | share of errors |
|---|---|
| dotted leaders (`..........` form blanks, collapsed to `...`) | **71%** |
| Bengali marks and letters | 15% |
| danda `।` read as `\|` or `৷` | 3% |
| everything else | 11% |

**Most of the "error" is a form artifact, not recognition.** A row of leader dots is a blank to be filled in; collapsing it changes no meaning. Quote 0.028 for text and 0.073 for the literal page, and say which. (The shares here are recomputed with the corrected alignment — the earlier 53/28/14/5 split was measured through `autojunk` and attributed characters to phantom replace blocks.)

The real misses are small and specific: `Particulars` read as `15` (a table header lost to the ruling beside it), and `ঃ` → `£ &`. **Bengali held up** — every distinct glyph on the page came back, which is the opposite of `word_bn.pdf`'s broken text layer where four characters were absent entirely.

**The honest caveat on the reference:** it was transcribed from the rendered page and cross-checked at 1.8× on three crops. That is what a human annotator does, but it is one transcriber and one page, so treat it as a first real data point rather than a benchmark. The truth file carries the PDF's `sha256` and the harness refuses to score a different file.

#### Three real scans, and a corpus that can grow

`scripts/find_scanned_pages.py` inventories every genuinely scanned page in the corpora — the gate decides, `has_images` decides `scanned` vs `empty`, so it cannot drift from the pipeline's own derivation, and nothing is rasterised. Run on the original corpus it found exactly **two** gradeable scans in 1748 pages, which is why `scripts/fetch_scans.py` now exists: it pulls candidates from the Internet Archive and **deletes any download whose pages pass the gate**, because most Archive PDFs carry an OCR text layer the Archive added and grading a recogniser against another recogniser's output measures agreement, not accuracy.

That took the corpus from 8 pages classified `scanned` to **1877 across 10 producers**, Bengali and Arabic, from 7-year-old Acrobat image plug-ins to ABBYY. Downloads land in the gitignored `.corpus/scans/`; `PROVENANCE.json` records every Archive identifier so the set rebuilds.

| page | script | producer | CER | sequence | overlap |
|---|---|---|---|---|---|
| `real_81_annexure` p0 | bengali+latin, ruled form | `SECnvtToPDF` | **0.073** | 0.956 | 0.916 |
| `scan_ben_vol24` p10 | bengali, continuous prose | `ABBYY FineReader 9.0` | **0.052** | 0.950 | 0.943 |
| `real_mou_signature` p13 | latin, two-column | `Acrobat Pro 2020` | 0.252 | 0.764 | **0.988** |

**`scan_ben_vol24` p10 is the one that matters most here** — a scanned Bengali novel page, the first real scan of continuous Bengali *prose* rather than a form, single-column so its sequence score needs no caveat. CER 0.052 with **46/46 distinct Bengali glyphs** recovered. Its residual errors are specific and worth knowing: the danda `।` read as `|`, typographic quotes substituted for straight ones, chandrabindu dropped (`সিঁড়ি` → `সিড়ি`, `হাঁটতে` → `হাটতে`), and **Tesseract occasionally emitting a Latin fragment where a Bengali word belongs** (`হ্যাঁ` → `Sl`, `ট্যাটনা` → `BBA`) — with `ben` alone, no `eng` in the language list.

**The MOU page is the counter-example that makes the metric legible.** Recognition is essentially perfect (0.988 overlap) and the CER is 0.252 anyway: the page is a two-column signature block whose four pairs interleave by `y`, so a column-wise reference and a row-wise recogniser agree on every character and disagree on where each belongs. That is the reading-order problem arriving inside the OCR score.

**Robustness, separately from accuracy:** 30 pages sampled across all 12 fetched documents, run with `--ocr`, **zero crashes**, median 1.6 s/page. Six recovered nothing, and all six are blank pages or photographed book covers — on the blanks the `--ocr-min-confidence` floor correctly rejected scanner speckle rather than returning it as text, which is the first time that guard has been demonstrated on real input rather than argued for.

**And the harness did not say so.** The reordering diagnosis existed only on the flattened-page path; the `--truth` path — the one grading a real scanner, the only evidence here that is not a ceiling — printed a 0.31 gap between the two metrics with nothing saying which to believe, while CLAUDE.md claimed it diagnosed exactly that. `diagnose()` is now one function called from both, and it distinguishes the two causes: against a native reference the gap usually means visual order, and against a hand-typed one it cannot, so it points at the truth file's `caveat` instead.

**`source_type: scanned` over-reports, and the inventory is what showed it.** Of 8 pages so classified across 1748, **6 are designed cover art** — NASA's FY2025 cover, two Saudi Ministry of Finance covers, an MCI back cover with a QR code. All satisfy the derivation exactly: no text layer, raster content present. The derivation is not wrong, but the name invites a consumer to route `scanned` pages to OCR, and on this corpus that OCRs six covers for every two real scans. Only `81_Annexure-1.pdf` p0 and `oct222013smespdl02_mou.pdf` p13 are scanned *text*.

The same sweep counts what `--ocr-repair` exists for: **67 pages carrying `RTL_VISUAL_ORDER` and 32 carrying `COMBINING_MARK_ORDER`** — far more than the scans, which is the measured form of the claim that this is a born-digital feature rather than a scanning one.

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

**`scanned` means "no text layer, raster content present" and nothing more.** A
designed cover page satisfies that exactly, and measured across the corpora
*three quarters of the pages carrying it are cover art rather than scans* — see
**A second real scan** above. Do not read it as "route this to OCR"; read it as
"this page's content is in its pixels", which is all the derivation can know.

**A zero-page PDF is rejected too**, in the same place. One of the real Arabic reports is a valid, unencrypted 14 MB `PDF 1.6` whose page tree resolves to nothing. Left alone, `process()` returned a `Document` with zero pages and no error, and the CLI exited **0** — because "every page produced a valid result" is vacuously true of no pages. A 14 MB file that produced nothing was being reported as a success.

**A document that forbids copying is reported, not obeyed and not ignored.** An owner password sets a permission bitfield excluding text extraction — and it is a claim the file makes rather than a lock it enforces, since the file opens without a password and essentially every tool ignores it. Docyx extracts it and emits `EXTRACTION_NOT_PERMITTED`, which is the same contract it applies to a text layer that lies: say what the document claims, let the human decide. Silent on ordinary files, which all report `copy_allowed`.

It is a **`Document.issues`** entry (schema v1.9), not a page warning: repeating it on all 807 pages of a report is noise, and putting it on page 1 hides it from anyone reading page 50 alone. The CLI summary prints document issues alongside page ones, or a warning nobody reads the JSON for is invisible — which would defeat warning rather than blocking.

**A password-protected PDF is rejected there too.** It opens cleanly and then every page raises, which reported `1 failed [PAGE_UNREADABLE]` — indistinguishable from a damaged file, so the user chases corruption instead of finding the password. Now exit 2 with "decrypt it first". An *owner* password is deliberately not refused: it restricts permissions rather than access, the file opens without one, and rejecting it would turn away a document anyone can read.

**The boundary was audited against hostile input**, each against the documented exit codes. A 0-byte file, a plausible header over garbage, a truncated PDF, and a PNG wearing a `.pdf` name all exit **2**; a password-protected file now does too. The contract held everywhere except the password case.

**Non-PDF input is rejected at the boundary** in `PDFRenderer`. PyMuPDF also opens
XPS, EPUB, CBZ and Office documents, so `fitz.open()` succeeding is not evidence of
a PDF — a `.docx` went through the whole pipeline reported as `ok` before this check
existed.

### The tagged-PDF path was measured and declined

§10 step 1 asks for native PDF structure, and `scripts/measure_struct_tree.py` reports 75.9% of the corpus tagged, 69% with useful tag types. Those are *per file* and they oversell it badly.

**Per page, heading tags cover 6%.** `nasa_budget.pdf` is 807 pages with headings declared on 9 of them; `wiki_bn.pdf` is 65 pages with 18. `wiki_bn` p5 — a page with a perfectly ordinary visible heading — declares **no heading tag at all**.

**The link from a tag to a line is the expensive part.** PyMuPDF exposes no MCID: not on spans, not in `get_texttrace()`, not in `get_bboxlog()`, and there is no structure API. The tree itself is walkable by xref (`/S /H1`, `/Pg`, `/K [0 …]`), but reaching the text under an MCID means tokenising the content stream — `/H1 <</MCID 0>>BDC … EMC` — where strings are CID-encoded (`<00E0> Tj`) and need the font to decode. Correlating marked-content order against MuPDF's span order instead can silently attach a heading type to the wrong line, which is the failure this codebase treats as worse than doing nothing.

**MuPDF's own `get_text("xhtml")` is not a shortcut.** It emits `<h1>`–`<h6>`, but from font heuristics rather than the tree: on `wiki_bn.pdf` p5 it claims seven `<h3>` where the file declares zero, and five of the seven are ordinary paragraphs.

So `analysis/headings.py` plus the manual control cover the same ground at a fraction of the risk. Revisit if a PDF backend appears that exposes MCIDs directly — that one missing accessor is what makes this expensive, not the idea.

### Known gaps

- **Scanned PDFs need `--ocr`**; without the flag a scanned page still fails, by design. **Three** real scans are graded against hand-typed references, across three producers and two scripts (CER 0.073 / 0.052 Bengali, 0.252 Latin — see above), and the corpus behind them now holds **1877 scanned pages from 10 producers** rather than 8. The binding limit is no longer documents, it is **references**: a page is only gradeable once a human types it, so three is what one transcriber produced. `fetch_scans.py` grows the pool; transcription is what turns a page into evidence. Skew and show-through are now represented in the corpus but not yet in any graded page.
- **Layout classification is a stub**: no `layout_region` is ever produced without an injected detector, and none ships. `TableAnalyzer` is the same seam and *does* have a working detector, so the pattern is proven rather than speculative. Tables additionally have a model-free path (**Tables with no model**); layout does not, and the measured failure of `DocLayNetDetector` on captions says a model is not obviously the answer there either.
- `figure` detection is a contour heuristic. Dense text used to be misreported as figures (434 of them in a 114-page RFC); a component-density filter now rejects candidates that fragment like text. Inject a real figure head via `detector` when precision matters.
- `visual_inference` provenance is unused, and the obvious producer was measured and declined — see below.

### Typography from pixels was measured and declined

An OCR'd line carries no typography at all: `OCRAnalyzer` emits `typography: None`, because a recogniser reports characters. That leaves the heading suggester dead on scanned pages, since it runs entirely on font size. `visual_inference` is the schema's reserved home for the fix — typography estimated from the image (§7, §20) — and the only signal available without a model is the recognised line's **box height**.

`scripts/measure_type_size.py` grades it: flatten a born-digital page, OCR it, match each recognised line back to the native line it covers, compare against the font size the PDF declares.

| page | height / font_size | size within 10% | "bigger than body" agrees |
|---|---|---|---|
| `arxiv_attention` p0 | 0.910 | 76% | 5 of 10 |
| `arxiv_attention` p2 | 0.915 | 73% | **0 of 4** |
| `rfc2616` p12 | 0.916 | 50% | **1 of 13** |
| `nasa_budget` p88 | 0.964 | 67% | 2 of 6 |
| `wiki_ar` p6 | **1.320** | **22%** | **0 of 28** |

**The median error is 1.5% on the best page, and that number is worthless** — it is the median, and a schema value is per element. A line's box runs ascender to descender, so whether it happens to contain a `g` moves its height more than a real size change does. Latin sits at 0.91 and Arabic at 1.32, so there is not even one constant to divide by.

**The ranking column is what kills it.** "Bigger than body" is the weaker claim and the only one the heading suggester needs, and box height *systematically over-reports*: `wiki_ar` p6 declares 2 such lines and pixel height claims 26. A suggester fed that would propose a heading on half the page.

So `visual_inference` stays reserved rather than emitted, and phase 6's criterion asking it to become active was struck rather than satisfied. Re-open it when a real estimator exists — a model that reads weight and size off the glyphs — not with a bounding box.

## Workspace

```bash
PYTHONPATH=. .venv/Scripts/python.exe -m docyx.workspace file.pdf [--layout] [--tables] [--ocr ben]
PYTHONPATH=. .venv/Scripts/python.exe -m docyx.workspace circulars/   # a folder, one session
```

Renders each page with its extracted elements drawn over it, an outline of the page in reading order beside it, and an inspector for whatever is selected. Boxes are coloured by `confidence.type` — green `exact`, blue `detected`, amber `inferred`, purple edited — so a page's trustworthiness is visible before reading anything.

**This exists as a feedback loop, not a feature**, and it keeps earning that. The phase 1–4 code review found 12 issues, three of them regressions that 219 passing tests missed — including `--layout` taking a page from 3 headings to zero — because no test rendered the output the way a user sees it. Every workspace change since has found more the same way: a font size printed as `11.039999961853027pt`, a selection scrim opaque enough to hide the page, a trust bar showing solid green on a `PARTIAL` page, a `413` that never reached the client, `Save`/`Cancel` visible with nothing to save, and labels burying the page under its own line fragments. **If you change this file, look at it rendered.** The tests cannot.

`http.server` from the stdlib, deliberately: one image, one JSON blob and one HTML file do not justify a fifth core dependency. The routes are thin so FastAPI can replace it when uploads, auth or concurrency arrive.

**The design tokens live in `:root` in [index.html](docyx/workspace/static/index.html) and nowhere else** — there is no `DESIGN.md`, because a second file listing the same hex values can only drift from the one the browser actually reads. The surface/ink/hairline/radius ladders are Linear's, taken from getdesign.md's public analysis; the landing-page half of that system (96px sections, 80px display type, card padding) was deliberately left behind, because this is a dense inspection surface and applying marketing rhythm to it is how the viewer would end up looking templated. Nothing third-party is vendored — no fonts, no CSS, just the numbers.

Three rules hold the UI together, and each one is load-bearing rather than decorative:

- **Monospace only where the interface quotes the machine.** Codes, values, coordinates and JSON are Consolas; anything Docyx says in its own voice is the UI sans. It is the provenance story — read values vs. computed ones — carried into type.
- **Lavender `--accent` is chrome, never data.** Focus rings previously used the `detected` blue, which made a focus ring and a detector's claim the same colour. For the same reason `edited` sits magenta-side of the lavender.
- **The tier hues are a schema contract**, so they stay saturated while everything around them is neutral. They *are* the data; the census in the footer counts them and doubles as the filter.

Selecting an element scrims the page around it rather than tinting it, so the element's own pixels stay at full contrast — checking a claim against the pixels it came from is the whole job.

**The outline pane is resizable, and that is what makes the extracted text readable at all.** At the old fixed 250px a Bengali line ellipsed after about four words, so the one pane that shows *what was extracted* could not be read — and proofreading that text is the job the workspace exists for. Drag the splitter, arrow-key it when focused, double-click to reset; the width persists in `localStorage`.

- **Past 330px the rows wrap and the list stops being an index and becomes the page's text.** Derived rather than chosen: below that a wrapped line is three ragged words per row, which is worse than the ellipsis it replaced. So one gesture covers both jobs and there is no second control to explain.
- **Clamped 190–640.** The floor keeps the reading-order number and the tier dot legible. The ceiling matters more: checking a claim against the pixels it came from is the whole point, so the pane can never squeeze the page out. Widening to 480 already drops `wiki_bn` p5 from 66% to 47%.
- **Resizing goes through the same double-draw as `setZoom`.** Narrowing the pane widens the bench, which changes the fit scale and can toggle the bench's scrollbar — measuring in the same tick reads the pre-reflow width. Draw now *and* after the reflow, never only after.
- **It is a splitter and not a set of preset widths**, because the useful width depends on the script and the page. A Latin page is legible at 250px; Bengali and Arabic are not, and hard-coding a second number would just be a worse guess.

**A flat "all the text" pane was considered and rejected.** It is what page-transcription tools generally offer, and it would drop the two things this list is for: the tier dot that says how much to trust each line, and click-to-select, which is the return half of the selection loop. Wrapping the existing rows keeps both and shows the same text. Same reasoning as everywhere else here — take the idea, not the layout.

**Zoom** (`−`/`+`/`0`, or the pill on the page) multiplies the fit scale rather than replacing it, so the percentage shown is the real one against the image. It was added because bbox handles shipped with no way to get close to them, which made small boxes uneditable in practice. The bench only gains a horizontal scrollbar above 1×, where the page genuinely is wider than it.

**The category is editable**, through a control in the inspector rather than free text: the vocabulary travels with the page (`/api/page` carries `types`), so the viewer cannot offer a type the server would refuse. `Suggest` applies the typography proposals above, one `retype` each, so every one is separately undoable.

**`Blocks` (`B`) is the resting view, and it is on by default.** Outlining every line buries the page it is drawn over — `rfc2616` p12 is 52 lines in 17 PyMuPDF blocks, `arxiv_attention` p2 is 27 in 8. **The block is already named in the element id** (`page13_b10_l0`), so grouping starts as a regex with no layout model. At rest only block outlines are drawn, and a line's own box appears when you point at it or select it, which is when its exact edges are what you want.

**"It is a regex" was measured only on English, and it is false on Arabic.** A PyMuPDF block is a paragraph in Latin and very nearly a line in Arabic:

| page | lines | id-grouped blocks | single-line blocks |
|---|---|---|---|
| `arxiv_attention` p2 | 27 | 8 | 2 of 8 |
| `rfc2616` p12 | 52 | 17 | 6 of 17 |
| `wiki_bn` p5 | 35 | 7 | 5 of 7 |
| **`wiki_ar` p6** | **57** | **44** | **33 of 44** |

So the view whose whole purpose is *not boxing every line* was still drawing 44 boxes for a handful of paragraphs — failing on exactly the scripts this project exists for, and invisible while the resting view was only ever checked in English. A second pass now merges block groups a reader would call one paragraph: **44 → 11 on `wiki_ar` p6**, against 22 → 17, 9 → 9 and 24 → 23 on English pages. Conservative where the regex already worked, decisive where it did not, which is the shape a fix for this should have.

- **The gap threshold is measured.** Over the median line height, the gap between two lines of one paragraph has a median of 0.10 / −0.11 / −0.01 / −0.20 across those four pages — at or below zero, because ascenders and descenders make line boxes touch. Paragraph breaks sit in the upper tail, p90 of 1.44 / 0.62 / 0.04 / 0.86. Half a line height separates them.
- **The guards matter more than the threshold**, because merging a heading into the paragraph beneath it would be a worse defect than the 44 boxes. A change of `type`, `direction` or font size stops a merge — and a heading *is* a font-size change. Verified on `wiki_ar` p6: every Arabic section heading keeps its own box.
- **Merging follows reading order, not geometry alone**, so a merge can only join neighbours the page itself calls adjacent. Geometry alone would bridge two columns.
- **It is a view grouping.** No element changes, ids stay positional, the schema is untouched, and `Lines` still shows every box.

A block takes the colour of its **weakest** line: a paragraph holding one `inferred` line must not read as `exact`. `Lines` restores per-line boxes for close work.

**`Labels` (`L`) tags each box `4. section_header`** — the same number the outline shows, so the two panes name an element identically. In block view a tag also says how many lines it covers (`16. text  8 lines`), which is 17 tags on that page instead of 52. Off by default, and **top-level only**: tagging a line's own style runs is the same over-listing the outline avoids.

**A light theme** rides the same tokens, with the tier hues *re-tuned rather than replaced* — same four meanings, darkened to hold against white paper instead of near-black. The choice persists in `localStorage`.

**Three stale-layout bugs, all the same shape**, worth knowing before touching `draw()`: sizing the canvas is what makes the scrollbar appear, which changes the width the fit was just measured against. Measuring in the same tick reads the pre-reflow value. Fixed by drawing *and* scheduling one more frame — never by deferring the only draw, because a background tab throttles `requestAnimationFrame` and the page then stays stale.

**Text editing is wired up.** `POST /api/edit?page=N` with `{id, text}` finds the element and calls `edit_text()` — the only writer, so every rule in **Human edits are provenance** holds unchanged: `source` stays put, `original_text` is written once, the element becomes `exact` / 1.0 and turns purple in the viewer.

- **The edit lands on the cached `Document`**, which makes the per-page cache the session's working copy rather than a speed trick. Navigating away and back keeps the correction; closing the process loses it, because nothing writes to disk yet.
- **Only whole lines are editable** — `state.top` in the viewer, i.e. no `text_span` and no `table_cell`. A span is a fragment of its line and a cell of its table, so correcting one would leave the parent's `text` stale and the two would disagree about the page. The server will happily edit a child by id (`_walk` descends, and a test pins that); the restriction is the UI's, and it is the line-granularity rule from §5 applied to writes.
- **Bbox editing (§10) is still absent**, and needs the `original_geometry` decision before it lands.

**Bbox editing** is `POST /api/move` → `Element.edit_geometry()`, the geometry twin of `edit_text()`. Drag the box to move it, the eight handles to resize. Corners are normalised rather than width/height clamped, so dragging an edge past its opposite flips the box instead of producing a negative size.

**Resizing a box re-reads the text inside it, and for three releases it did not.** A box is a claim about *which glyphs these are*, so moving one without re-reading leaves the element asserting a region and a string that came from a different region — grow a line box to take in the word the extractor clipped and the word stayed out of the text, which is the entire reason anyone drags the handle. `NativeTextExtractor.text_in()` reads the region (in 150-DPI reference pixels, converted once inside `docyx/pdf/`, so PyMuPDF stays contained) and `move()` applies it through `edit_text`.

- **Both mutations sit under one `_record`**, so the gesture is one undo. A person dragged a handle once.
- **`original_text` still holds the machine's own claim** from before the drag, because the re-read goes through `edit_text` rather than assignment — and `edit_text`'s first-edit-only guard means a second drag cannot overwrite it with the first drag's result. Pinned by `test_a_re_read_box_keeps_the_original_from_the_FIRST_drag`.
- **Native text only.** An OCR line's text is in no text layer, so re-reading it would silently blank it; recognising the new crop is the real answer there and needs the detector. Pinned by `test_a_non_native_element_is_never_re_read_from_the_text_layer`.
- **Style-run children are dropped**, because they described the old box. Left alone they are a line whose own children contradict it. `patch()` in the viewer copies `children` for the same reason — without it the canvas kept drawing the old runs inside the new box.
- **A box dragged onto nothing now holds nothing**, which is what the region says. `test_check_reports_a_defect_a_human_edit_created` gained `EMPTY_TEXT` beside `ZERO_SIZE_BOX` when this landed — the check report finally seeing a state it was written for and could not reach, because the text still looked fine.

`Provenance.original_geometry` is the reason for **schema v1.7**. It is guarded *separately* from `original_text`: the obvious implementation gates both on `modified_by_user`, and then whichever edit came second records nothing — correcting the text of a box you already moved would silently discard the geometry the machine proposed. Pinned by `test_a_text_edit_never_overwrites_a_geometry_original`. The same bug existed in `edit_text` alone and is now fixed: its guard is `original_text is None`, with `or ""` so an element that had no text still records that it had none rather than letting the *second* edit claim the first correction as the original.

**Undo/redo restores a snapshot; it never re-applies an edit in reverse.** `_snapshot` deep-copies text, geometry, confidence and provenance together, because "edit it back" leaves `modified_by_user` set and the element `exact` / 1.0 — claiming a human vouched for a value they just took back. History is **per page**: a global stack would make Ctrl+Z reach into a page the user has already left. An empty history is `409`, a normal state rather than a fault.

**A folder of PDFs opens as one session.** `python -m docyx.workspace circulars/` builds a `DocumentSet` and the filename in the header becomes a picker; a single PDF behaves exactly as before and shows no picker at all.

- **A switched-away document keeps its `Workspace`, and therefore its edits.** The per-page cache *is* the session's working copy, so rebuilding it on a switch would silently discard every correction made there. The picker marks documents holding unexported work with a dot — the cliff is closing the process, not changing documents. *ponytail: memory grows with documents opened; add an LRU that refuses to evict an edited document if someone opens a folder of thousands.*
- **`edited()` walks cached pages only.** Answering it properly would mean extracting every document in the folder to populate the picker, which is a full run per file before you have looked at one. A page nobody has opened cannot have been edited.
- **This is not the two-folder-plus-Sync design it was modelled on.** That shape exists because the tool it comes from keeps page images and annotations as separate artifacts that can drift apart. Here one PDF produces both, so there is nothing to keep in step and no Sync control to build. Copying it would have added a control that can never be out of sync.

**The page check report** is [docyx/analysis/check.py](docyx/analysis/check.py) → `GET /api/check?page=N` → cards in the inspector. Four faults that a `Document` can carry while validating perfectly and exporting without complaint, which is exactly why they need their own panel rather than a place beside the gate's `PageIssue`s: `ZERO_SIZE_BOX`, `BOX_OFF_PAGE`, `EMPTY_TEXT`, `STACKED_BOXES`.

- **There is no Check button, deliberately.** It is pure geometry over an already-cached page, so it runs on every page load and after every mutation — `send()` in the viewer is the single funnel, so that is one call site. A check you have to remember to press is a check nobody presses, and a report left over from before an edit is worse than none because it reports a defect you just fixed.
- **Every threshold is measured, not chosen.** `BOX_OFF_PAGE` allows 2% of page width (~25px, about one line height at 150 DPI) because `wiki_ar.pdf` p6 has 31 justified RTL lines starting left of zero and the worst is 11.9px out — while every other corpus page overshoots by exactly 0. At zero tolerance the check put 31 findings on a page with nothing wrong with it.
- **`STACKED_BOXES` compares same-type boxes only.** A line sits wholly inside its own `layout_region` by design, and reporting that would bury every real duplicate. On real input it fires once in the corpus, on `irs_f1040.pdf` p0: the `/` date separators are extracted as their own lines inside the wider line's box. That is genuine over-segmentation — the failure class CLAUDE.md elsewhere calls ungradeable — so this catches a slice of something otherwise unmeasured.
- **Findings name elements and never change them**, and each id is a chip that selects that element, because a report you cannot act on from is a traceback with better typography.
- **The card takes the lavender chrome accent, not a tier hue.** A finding is Docyx speaking in its own voice, not data; the tier colours are a schema contract and stay out of it.

`test_real_pages_stay_clean` pins the false-positive rate: a check that fires on ordinary pages is a check nobody will read.

**Export** is `POST /api/export` → `<stem>.docyx.json` beside the PDF.

- **Validate, then write.** A file that fails its own schema must not exist, so `Document.model_validate` runs first and a failure is `422` with nothing written. Pydantic does not validate on assignment, so this is a real check rather than a tautology.
- **"Errors block export" means *schema* errors.** A `failed` page does not block: partial results are the documented contract, and refusing to export 199 good pages over one bad one would invert it.
- **Every page is exported, not just the visited ones**, so editing page 1 of a 200-page report still costs a full extraction run at export time.

**The per-page cache is locked, and not for speed.** `ThreadingHTTPServer` serves requests concurrently and this cache *is* the session's working copy, so populating it is a read-modify-write two requests can race. Without the lock, two concurrent requests for an **uncached** page each built their own `Document`; one won the dict slot and the rest were discarded — so an edit applied through a loser was accepted, **answered `200` with the correction in it, and silently never reached the page**. Reproduced 6 trials out of 6. It also ran six full extractions for one page, which with a layout or OCR model is six times the model cost.

The lock is reentrant because every mutator calls `find()` → `page()` beneath itself, and it wraps the mutators too: record-then-apply is its own read-modify-write. Pinned by `test_concurrent_edits_to_an_uncached_page_are_not_lost`.

**Edits live in the process.** The per-page cache is the session's working copy; export is the only way out. There is no resume — and `scripts/measure_id_stability.py` now says exactly why, rather than leaving it a worry.

**Element ids are positional** (`page1_b0_l6` is block 0, line 6 of PyMuPDF's enumeration), which makes them stable against everything the *runtime* varies and fragile against everything the *code* does. Measured over 1092 elements across 4 documents:

| | result |
|---|---|
| unique within a page | 0 collisions |
| deterministic across runs | 0 lost, 0 moved |
| `pages=[n]` vs whole-document run | 0 lost, 0 moved |
| layout + table + visual detectors added | 0 native text ids moved |

So a same-version round trip is safe, and so is an edit made in the viewer reattached to a CLI batch export. Then `--against REF` builds a worktree at an old commit and diffs:

```
4b9b461 -> HEAD
  arxiv_attention.pdf   127 -> 80   ids   kept  22  lost 105  new  58  moved  1
  wiki_ar.pdf           178 -> 200  ids   kept 151  lost  27  new  49  moved 28
```

The span-to-line granularity change **orphaned 83% of one page's ids**, and — worse — 29 ids *survived while naming different content*. One of those is traceable to the "blank spans must not be skipped" fix: `page1_b16_l0_s1` went from `Equal contribution.Listing order` to `Equal contribution. Listing order`. A resume keyed on id alone would have silently reattached a human correction to text that had changed underneath it.

**So resume verifies content, not the id alone** — `POST /api/import` → `Workspace.restore()`, and the export *is* the save file, so there is no second format to drift.

**The check is `original_text` / `original_geometry` against what the extractor says now.** Those record the machine's own claim at the moment of the edit; if it still holds, the edit still applies. Comparing the *corrected* text would be useless — it differs by construction. Four outcomes, and only the first writes anything:

| | |
|---|---|
| original matches | reattach, through `edit`/`move`, so the import is undoable |
| already carrying this exact edit | `unchanged` — without it, importing twice reports every edit as a conflict, because the target no longer matches its own `original_text` |
| original differs | `EDIT_CONFLICT`, never auto-applied |
| id gone | `EDIT_ORPHANED` |

Conflicts and orphans render as page issues beside the gate warnings, naming both values. An import that silently dropped what it could not place would be worse than no import: the edits are gone and nothing says so.

Only `modified_by_user` elements are carried over. Re-applying every element would rewrite the page from a stale file.

Run `--against` before shipping any extraction change to see how much it would orphan.

No revert button, deliberately: `original_text` makes one trivial, but re-applying it through `edit_text()` lands in exactly the state undo exists to avoid. Undo is the revert.

## Planning docs

`.planning/` (GSD workflow: `ROADMAP.md`, `STATE.md`, per-phase dirs) tracks the 6-phase roadmap. **Phases 1–5 are complete**; the CLI, listed under phase 5, shipped early in phase 4 because being fast and light buys nothing while the tool is import-only.

**Phase 6's `visual_inference` criterion was measured and struck.** See **Typography from pixels** below; the roadmap now asks only that `ocr` become active, which it is.

**Every capability now has a scorer.** Reading order (8 pages), semantic roles (5), OCR (4 flattened + 1 real scan), table structure (3 tables) and table *region* detection (9 negative pages). What is left is *more* evidence rather than the first of it, and the shape of the remaining risk is known: each widening of the table-region negative set produced a new false-positive class, so that number is the one to grow next. The two root markdown plans are the authoritative spec and cross-reference each other by section number — read together, neither is self-contained:

- `universal_page_document_metadata_extraction_plan_v6.md` — architecture, pipeline, schema
- `universal_page_metadata_extraction_tool_uiux_plan_v3.md` — the phase-5 workspace UI

`AGENTS.md` is **stale** — it claims the repo is planning-only with no code. Its v1 scope constraints and architecture notes are still accurate; ignore the "no code yet" framing.

## Licensing

**AGPL-3.0** (`LICENSE`), because PyMuPDF is AGPL-or-commercial and the project's stated differentiator is open-source self-hosted operation. See [LICENSING.md](LICENSING.md) for the dependency inventory and what the choice forecloses — notably a proprietary hosted API or closed enterprise deployment, both of which appear in §24's commercialization sketch.

PyMuPDF is confined to [docyx/pdf/](docyx/pdf/); everything outside depends on the protocols in [docyx/pdf/protocols.py](docyx/pdf/protocols.py), and `test_pymupdf_stays_inside_docyx_pdf` fails the build if that stops being true. It had already leaked: the pipeline held a raw `fitz.Document` as `renderer.doc` and passed it to two collaborators, so the backend type was in the hands of a module meant to know only the protocols. `PDFRenderer.text_extractor()` and `.text_document()` close it. **Keep it that way** — that containment is what makes the licence decision reversible for the cost of one package. Any injected layout/table model brings its own licence; that audit is still outstanding.

## v1 scope limits (non-negotiable)

PDF-only input, reject non-PDF at upload. No GPU, no bundled model weights. **A machine-readable text layer is still the condition for `ok`** — OCR is opt-in and its output is `partial` / `inferred`, which keeps that limit intact rather than repealing it.

"No OCR" was a v1 limit until the owner lifted it; the resource constraint behind it was not lifted, which is why the recogniser is a 5 MB binary behind an optional dependency and not a torch stack.
