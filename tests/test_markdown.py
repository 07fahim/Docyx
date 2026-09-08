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


@pytest.mark.xfail(
    reason="Reading order is a raster sort on (y, x), not an XY-cut, so columns "
    "interleave line by line. This test documents the target behaviour and will "
    "pass once ReadingOrderCalculator does real column detection.",
    strict=True,
)
def test_two_column_page_reads_down_each_column(tmp_path):
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
