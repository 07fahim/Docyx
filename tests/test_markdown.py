import fitz
import pytest

from docyx.analysis.tables import CellDetection, TableAnalyzer, TableDetection
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.export import to_markdown
from docyx.pipeline.extractor import DocyxPipeline
from docyx.schema.errors import PageIssue
from docyx.schema.models import Document, Element, Page, PageStatus, Typography

BOLD = 1 << 4


def _text(text, y, size=11.0, flags=0, order=None):
    return Element(
        id=f"t{y}",
        type="text",
        geometry=Geometry(bbox=BoundingBox(x=10.0, y=y, width=100.0, height=12.0)),
        confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
        provenance=Provenance(source=ProvenanceSource.NATIVE_PDF),
        text=text,
        reading_order=order,
        typography=Typography(font_size=size, flags=flags),
    )


def _page(elements, **kw):
    return Page(page_number=1, status=PageStatus.OK, width=1000, height=1400, elements=elements, **kw)


def _doc(page):
    return Document(document_id="d", pages=[page])


def test_largest_font_becomes_h1_and_body_stays_prose():
    page = _page([
        _text("Annual Report", y=10, size=24.0, order=1),
        _text("Body sentence one.", y=50, size=11.0, order=2),
        _text("Body sentence two.", y=70, size=11.0, order=3),
    ])
    md = to_markdown(_doc(page))

    assert "# Annual Report" in md
    # Consecutive body spans join into one paragraph rather than one line each.
    assert "Body sentence one. Body sentence two." in md


def test_heading_levels_rank_by_size():
    page = _page([
        _text("Title", y=10, size=28.0, order=1),
        _text("Section", y=40, size=18.0, order=2),
        _text("Body text here.", y=70, size=11.0, order=3),
        _text("More body text.", y=90, size=11.0, order=4),
    ])
    md = to_markdown(_doc(page))

    assert "# Title" in md
    assert "## Section" in md
    assert "Body text here." in md
    assert "# Body text here." not in md


def test_reading_order_drives_output_order_not_position():
    """The whole point of the export as an evaluation instrument: if reading
    order is wrong, the prose is visibly wrong."""
    page = _page([
        _text("SECOND.", y=10, order=2),
        _text("FIRST.", y=500, order=1),
    ])
    md = to_markdown(_doc(page))

    assert md.index("FIRST.") < md.index("SECOND.")


def test_bold_short_line_becomes_strong_not_a_heading():
    page = _page([
        _text("Note", y=10, size=11.0, flags=BOLD, order=1),
        _text("Regular body copy.", y=30, size=11.0, order=2),
        _text("More regular copy.", y=50, size=11.0, order=3),
    ])
    md = to_markdown(_doc(page))

    assert "**Note**" in md
    assert "#" not in md


def test_table_renders_as_a_markdown_grid():
    detector = lambda _: [
        TableDetection(
            bbox=BoundingBox(x=0.0, y=0.0, width=200.0, height=100.0),
            score=0.9,
            cells=[
                CellDetection(BoundingBox(x=0.0, y=0.0, width=100.0, height=50.0), row=0, column=0),
                CellDetection(BoundingBox(x=100.0, y=0.0, width=100.0, height=50.0), row=0, column=1),
                CellDetection(BoundingBox(x=0.0, y=50.0, width=100.0, height=50.0), row=1, column=0),
                CellDetection(BoundingBox(x=100.0, y=50.0, width=100.0, height=50.0), row=1, column=1),
            ],
        )
    ]
    table = TableAnalyzer(detector=detector).analyze(b"png")[0]
    table.children[0].text = "Product"
    table.children[1].text = "Price"
    table.children[2].text = "Apple"
    table.children[3].text = "100"

    md = to_markdown(_doc(_page([table])))

    assert "| Product | Price |" in md
    assert "| --- | --- |" in md
    assert "| Apple | 100 |" in md


def test_table_without_structure_is_flagged_not_dropped():
    detector = lambda _: [
        TableDetection(bbox=BoundingBox(x=0.0, y=0.0, width=10.0, height=10.0), score=0.5)
    ]
    table = TableAnalyzer(detector=detector).analyze(b"png")[0]

    md = to_markdown(_doc(_page([table])))

    assert "structure not recovered" in md


def test_failed_page_becomes_a_comment_and_is_never_silently_dropped():
    failed = Page(
        page_number=2,
        status=PageStatus.FAILED,
        width=1000,
        height=1400,
        errors=[PageIssue(code="NO_TEXT_LAYER", stage="text_layer_detection", message="x")],
    )
    doc = Document(document_id="d", pages=[_page([_text("Page one.", y=10, order=1)]), failed])

    md = to_markdown(doc)

    assert "Page one." in md
    assert "NO_TEXT_LAYER" in md
    assert "page 2" in md


def test_suspect_text_layer_is_surfaced_in_the_output():
    page = _page(
        [_text("Possibly garbled.", y=10, order=1)],
        warnings=[PageIssue(code="TEXT_LAYER_SUSPECT", stage="text_layer_quality", message="x")],
    )
    md = to_markdown(_doc(page))

    assert "TEXT_LAYER_SUSPECT" in md


def test_end_to_end_from_a_real_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Quarterly Report", fontname="hebo", fontsize=22)
    page.insert_text((72, 150), "Revenue grew this quarter.", fontsize=11)
    page.insert_text((72, 170), "Costs stayed flat.", fontsize=11)
    pdf = tmp_path / "report.pdf"
    doc.save(str(pdf))
    doc.close()

    md = to_markdown(DocyxPipeline().process(str(pdf), "d"))

    assert "# Quarterly Report" in md
    assert "Revenue grew this quarter." in md
    assert md.index("Quarterly Report") < md.index("Revenue grew")


def test_empty_document_does_not_crash():
    assert to_markdown(Document(document_id="d", pages=[])).strip() == ""


def test_two_column_page_reads_down_each_column(tmp_path):
    """Was an xfail while reading order was a raster sort. The XY-cut finds the
    gutter, so each column is now read top to bottom before the next begins."""
    doc = fitz.open()
    page = doc.new_page()
    left = ["Machine learning models", "require large amounts of", "carefully labelled data."]
    right = ["This constraint often", "dominates the total cost", "of a research project."]
    for i, (l, r) in enumerate(zip(left, right)):
        page.insert_text((60, 120 + i * 18), l, fontsize=11)
        page.insert_text((320, 120 + i * 18), r, fontsize=11)
    pdf = tmp_path / "columns.pdf"
    doc.save(str(pdf))
    doc.close()

    md = to_markdown(DocyxPipeline().process(str(pdf), "d"))

    assert "Machine learning models require large amounts of carefully labelled data." in md
    assert "This constraint often dominates the total cost of a research project." in md


def test_line_break_hyphens_are_rejoined():
    """PDF stores what was drawn, so a wrapped word arrives as two lines.
    Joining on a space gives 'arbi- trary', a corrupted token to any tokenizer,
    retrieval index or training target downstream.

    Observed on arxiv_bert.pdf p3: arbi-/trary, in-/put, sin-/gle, clas-/
    sification, ag-/gregate, embed-/ding, visualiza-/tion — seven in one page.
    """
    from docyx.export.markdown import _join_lines

    assert _join_lines(["an arbi-", "trary span"]) == "an arbitrary span"
    # A hyphen before a capital or a digit is not a line break.
    assert _join_lines(["the GPT-", "3 model"]) == "the GPT- 3 model"
    assert _join_lines(["plain", "lines"]) == "plain lines"


def test_a_split_heading_does_not_become_two_headings():
    """'3.1  Pre-training BERT' is set as a number and a title with a wide gap,
    which PyMuPDF reports as two lines on one baseline. Rendered separately the
    document grows a phantom section called '3.1'."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "3.1", fontsize=16)
    page.insert_text((110, 100), "Pre-training BERT", fontsize=16)
    # Body must outnumber the heading: _body_size takes the most common size,
    # so a page that is mostly heading has no headings at all.
    for i in range(5):
        page.insert_text((60, 140 + i * 18), f"Body line {i} at the usual size.", fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()

    md = to_markdown(DocyxPipeline().process(pdf_bytes, "d"))

    assert "3.1 Pre-training BERT" in md
    assert "# 3.1\n" not in md and "**3.1**" not in md


def test_an_indented_line_starts_a_new_paragraph():
    """Without this every column collapses into one block of prose. The first
    line indent is the only paragraph signal present in the geometry."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "First paragraph opening line here.", fontsize=11)
    page.insert_text((60, 118), "continuing at the same left edge.", fontsize=11)
    page.insert_text((78, 136), "Second paragraph, indented.", fontsize=11)
    page.insert_text((60, 154), "and its continuation line.", fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()

    md = to_markdown(DocyxPipeline().process(pdf_bytes, "d"))

    assert "First paragraph opening line here. continuing at the same left edge." in md
    assert "Second paragraph, indented. and its continuation line." in md


def test_a_table_row_renders_as_one_row_not_one_block_per_cell():
    """Table detection is a stub, so a table's cells arrive as plain text
    elements sharing a baseline. Rendered individually each cell became its own
    paragraph — an 8-column table turned into 8 blocks per row.

    Measured on arxiv_gpt3.pdf p7, whose hyperparameter table now reads
    'GPT-3 Small 125M 12 768 12 64 0.5M 6.0 x 10-4' as a single line.
    """
    doc = fitz.open()
    page = doc.new_page()
    for row in range(3):
        for col, value in enumerate(["alpha", "10", "20", "30"]):
            page.insert_text((60 + col * 120, 100 + row * 20), f"{value}{row}", fontsize=11)
    for i in range(6):
        page.insert_text((60, 200 + i * 18), f"Ordinary prose line {i} follows.", fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()

    md = to_markdown(DocyxPipeline().process(pdf_bytes, "d"))

    assert "alpha0 100 200 300" in md
    assert "alpha1 101 201 301" in md
    # Rows stay on separate lines rather than flowing into one paragraph.
    assert "alpha0 100 200 300 alpha1" not in md


def test_a_run_in_heading_does_not_swallow_the_sentence_after_it():
    """Guards the over-correction: grouping by baseline alone let a bold
    heading absorb the sentence starting on its line, producing
    '**Input/Output Representations To make BERT**'. Only neighbours with the
    same style role may merge."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "Heading Here", fontsize=11, fontname="hebo")
    page.insert_text((160, 100), "and the sentence that runs on from it", fontsize=11)
    for i in range(6):
        page.insert_text((60, 130 + i * 18), f"More body text on line {i}.", fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()

    md = to_markdown(DocyxPipeline().process(pdf_bytes, "d"))

    assert "**Heading Here**" in md
    assert "**Heading Here and the sentence" not in md


def test_single_leading_prose_is_not_merged_into_one_line():
    """Regression: _visual_lines originally treated ANY vertical overlap as
    'same baseline'. PyMuPDF line boxes span ascender to descender, so at
    single leading (11pt text on an 11pt pitch) consecutive body lines overlap
    and were merged into one element.

    Two silent consequences: the merge joined with a bare space, bypassing
    _join_lines so hyphen repair never ran; and `parts` climbed past
    TABULAR_PARTS so an ordinary paragraph was emitted as a table row.

    Every fixture here used 18-20pt spacing for 11pt text, which is why the
    suite missed it. This one uses the tight case deliberately.
    """
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(
        ["An example of arbi-", "trary text that wraps", "across several lines", "at single leading."]
    ):
        page.insert_text((60, 100 + i * 11), line, fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()

    md = to_markdown(DocyxPipeline().process(pdf_bytes, "d"))

    assert "arbitrary text that wraps" in md, "hyphen repair was bypassed by the merge"
    assert "arbi- trary" not in md


# --- regressions the phase review caught -----------------------------------


def _lr_region(kind, x=0, y=0, w=500, h=500):
    return Element(
        id=f"r_{kind}", type=kind,
        geometry=Geometry(bbox=BoundingBox(x=x, y=y, width=w, height=h)),
        confidence=Confidence(value=0.9, type=ConfidenceType.DETECTED),
        provenance=Provenance(source=ProvenanceSource.LAYOUT_MODEL),
    )


def _lr_line(text, size=10.0, kind="text", y=0.0):
    return Element(
        id=f"l_{text[:4]}_{y}", type=kind,
        geometry=Geometry(bbox=BoundingBox(x=0, y=y, width=200, height=10)),
        confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
        provenance=Provenance(source=ProvenanceSource.NATIVE_PDF),
        text=text, typography=Typography(font_size=size), reading_order=int(y) + 1,
    )


def _lr_page(elements):
    return Page(page_number=1, status=PageStatus.OK, width=600, height=800, elements=elements)


def test_a_semantic_role_outranks_font_size():
    """_assign_roles retypes headings, and the size histogram filtered on
    type == "text" — so enabling the layout model took arxiv_bert p0 from 3
    headings to 0. The layout model must improve the export, not break it."""
    md = to_markdown(Document(document_id="d", pages=[_lr_page([
        _lr_line("A Real Title", size=10.0, kind="title", y=0),
        _lr_line("body text here", size=10.0, y=20),
    ])]))

    assert "# A Real Title" in md
    assert "body text here" in md


def test_a_layout_region_does_not_emit_a_table_or_figure():
    """DocLayNet types a region "table"/"picture" too. Without a provenance
    check the export emits a spurious empty-table comment per region."""
    md = to_markdown(Document(document_id="d", pages=[_lr_page([
        _lr_line("body", y=0), _lr_region("table"), _lr_region("picture"),
    ])]))

    assert "table detected" not in md
    assert "![figure]" not in md


def test_a_detected_picture_still_renders():
    """The figure test was `{"figure", "image"}`; nothing emits "image" and
    DocLayNet emits "picture", so every detected picture vanished."""
    picture = Element(
        id="p1", type="picture",
        geometry=Geometry(bbox=BoundingBox(x=10, y=10, width=50, height=50)),
        confidence=Confidence(value=0.8, type=ConfidenceType.DETECTED),
        provenance=Provenance(source=ProvenanceSource.GEOMETRY_INFERENCE),
    )
    md = to_markdown(Document(document_id="d", pages=[_lr_page([_lr_line("body"), picture])]))

    assert "![figure]" in md
