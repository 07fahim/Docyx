import pytest
import fitz
from docyx.core.constants import SCALE
from docyx.pdf.renderer import PDFRenderer
from docyx.pipeline.extractor import DocyxPipeline
from docyx.schema.models import PageStatus

@pytest.fixture
def sample_pdf_path(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Hello, World!")
    pdf_path = tmp_path / "sample.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return str(pdf_path)

def test_pipeline_extraction(sample_pdf_path):
    pipeline = DocyxPipeline()
    doc_model = pipeline.process(sample_pdf_path, document_id="doc_123")
    
    assert doc_model.document_id == "doc_123"
    assert len(doc_model.pages) == 1
    
    page = doc_model.pages[0]
    assert page.page_number == 1
    assert page.status == PageStatus.OK
    assert len(page.elements) > 0
    
    # Verify exact confidence and scaling
    text_el = page.elements[0]
    assert text_el.type == "text"
    assert "Hello, World!" in text_el.text
    assert text_el.confidence.value == 1.0
    assert text_el.confidence.type.value == "exact"
    assert text_el.provenance.source.value == "native_pdf"
    
    # Original insertion was at (50, 50). At 150 DPI (scale = 150/72 = 2.0833)
    # The new scaled coordinates should be ~104
    assert text_el.geometry.bbox.x > 100

def test_element_ids_are_stable_and_unique(tmp_path):
    # Two identical strings on one page previously collided (hash(text) as suffix)
    # and shifted between processes under hash randomization.
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Repeat")
    page.insert_text((50, 300), "Repeat")
    pdf_path = tmp_path / "repeat.pdf"
    doc.save(str(pdf_path))
    doc.close()

    ids = [el.id for el in DocyxPipeline().process(str(pdf_path), "d").pages[0].elements]
    assert len(ids) == len(set(ids))
    # Pinned exactly: any reintroduction of a hash-derived suffix breaks this.
    assert ids == ["page1_b0_l0", "page1_b1_l0"]


def test_pipeline_gate_failure(tmp_path):
    # A page with no text layer must fail the gate.
    doc = fitz.open()
    doc.new_page()
    pdf_path = tmp_path / "blank_doc.pdf"
    doc.save(str(pdf_path))
    doc.close()
    
    pipeline = DocyxPipeline()
    doc_model = pipeline.process(str(pdf_path), document_id="doc_fail")
    
    page = doc_model.pages[0]
    assert page.status == PageStatus.FAILED
    assert len(page.elements) == 0
    assert len(page.errors) == 1
    # Structured, not a JSON string consumers have to re-parse.
    assert page.errors[0].code == "NO_TEXT_LAYER"
    assert page.errors[0].stage == "text_layer_detection"


def test_typography_is_extracted(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "Styled", fontname="hebo", fontsize=17)
    pdf_path = tmp_path / "styled.pdf"
    doc.save(str(pdf_path))
    doc.close()

    el = DocyxPipeline().process(str(pdf_path), "d").pages[0].elements[0]

    assert el.typography is not None
    assert el.typography.font_size == 17
    assert "Bol" in el.typography.font_family  # Helvetica-Bold
    assert el.typography.flags is not None
    assert el.typography.color is not None
    # Point size, deliberately NOT scaled into the 150 DPI geometry space.
    assert el.geometry.bbox.height > el.typography.font_size


def test_detector_failure_degrades_page_to_partial(tmp_path):
    def exploding(image_bytes):
        raise RuntimeError("model weights missing")

    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "Still readable")
    pdf_path = tmp_path / "ok.pdf"
    doc.save(str(pdf_path))
    doc.close()

    from docyx.analysis.layout import LayoutAnalyzer

    page = (
        DocyxPipeline(layout_analyzer=LayoutAnalyzer(detector=exploding))
        .process(str(pdf_path), "d")
        .pages[0]
    )

    # Text survived, so the page is usable — but incomplete.
    assert page.status == PageStatus.PARTIAL
    assert len(page.elements) > 0
    assert [w.code for w in page.warnings] == ["STAGE_FAILED"]
    assert page.warnings[0].stage == "layout_detection"
    assert page.errors == []


@pytest.mark.parametrize("size", [(612, 792), (595, 842), (842, 1191)])
def test_page_extent_matches_the_rendered_image(tmp_path, size):
    """Element coordinates live in pixmap space, so Page.width/height must be
    the pixmap's, not int(points * SCALE).

    Rendering rounds where int() truncates: A4 reported 1239x1754 for an image
    that is 1240x1755, so a box on the right margin could exceed the page's own
    declared width. Letter divides exactly, which is why every existing fixture
    passed. Parametrised over Letter, A4 and A3 so one lucky page size cannot
    hide it again.
    """
    width_pt, height_pt = size
    doc = fitz.open()
    page = doc.new_page(width=width_pt, height=height_pt)
    page.insert_text((72, 100), "Text", fontsize=11)
    expected = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE))
    pdf = tmp_path / "sized.pdf"
    doc.save(str(pdf))
    doc.close()

    result = DocyxPipeline().process(str(pdf), document_id="sized").pages[0]

    assert (result.width, result.height) == (expected.width, expected.height)


def test_a_non_pdf_is_rejected_rather_than_processed(tmp_path):
    """§ v1 scope: PDF-only input, reject non-PDF.

    PyMuPDF opens far more than PDF — XPS, EPUB, and Office documents — so
    `fitz.open` succeeding is not evidence the input is a PDF. A .docx came
    through the CLI and was reported as '1 pages - 1 ok', which is exactly the
    silent wrong answer the scope limit exists to prevent: every coordinate,
    every provenance claim and the schema's whole meaning assume a PDF page.
    """
    not_a_pdf = tmp_path / "notes.txt"
    not_a_pdf.write_bytes(b"%!PS-Adobe-3.0\nplain text, definitely not a PDF\n")

    with pytest.raises(ValueError, match="not a PDF"):
        DocyxPipeline().process(str(not_a_pdf), document_id="nope")


def test_a_page_with_no_text_layer_is_not_called_born_digital(tmp_path):
    """source_type was in the published schema, defaulted to 'born_digital',
    and never assigned — so a scanned page, the one case the field exists to
    mark, reported the opposite of the truth. A consumer asking "which pages
    need OCR?" got the wrong answer on every one."""
    doc = fitz.open()
    page = doc.new_page()
    blank = fitz.open()
    pix = blank.new_page().get_pixmap()
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    pdf = tmp_path / "scan.pdf"
    doc.save(str(pdf))
    doc.close()

    result = DocyxPipeline().process(str(pdf), document_id="scan").pages[0]

    assert result.status is PageStatus.FAILED
    assert result.source_type == "scanned"


def test_born_digital_pages_still_say_so(sample_pdf_path):
    page = DocyxPipeline().process(str(sample_pdf_path), document_id="d").pages[0]
    assert page.source_type == "born_digital"


def test_one_unreadable_page_does_not_lose_the_whole_document(tmp_path, monkeypatch):
    """The stated invariant is 'partial results, never reject the whole
    document', but only the three detectors were wrapped. A raise from
    rendering or from text extraction propagated out of process(), so the
    caller got an exception instead of a Document with one failed page —
    losing every other page in a 200-page report to one corrupt one."""
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 100), f"Page {i} text.", fontsize=11)
    pdf = tmp_path / "three.pdf"
    doc.save(str(pdf))
    doc.close()

    from docyx.pdf import text_extractor as te

    original = te.NativeTextExtractor.extract_page

    def explode(self, page_num):
        if page_num == 1:
            raise RuntimeError("corrupt content stream")
        return original(self, page_num)

    monkeypatch.setattr(te.NativeTextExtractor, "extract_page", explode)

    document = DocyxPipeline().process(str(pdf), document_id="three")

    assert len(document.pages) == 3, "the good pages must survive"
    assert [p.status for p in document.pages] == [
        PageStatus.OK,
        PageStatus.FAILED,
        PageStatus.OK,
    ]
    assert document.pages[1].errors[0].code == "EXTRACTION_FAILED"
    assert "corrupt content stream" in document.pages[1].errors[0].message


def test_a_pdf_with_no_pages_is_rejected():
    """Found on a real 14 MB Arabic government annual report: a valid,
    unencrypted PDF whose page tree yields nothing.

    Without this the pipeline returns a Document with zero pages and no error,
    and the CLI exits 0 — "every page produced a valid result" is vacuously
    true of no pages. A file that produced nothing must not report success.

    The fixture is written by hand because PyMuPDF refuses to SAVE a zero-page
    document ("cannot save with zero pages") while opening one quite happily.
    """
    empty = b"\n".join(
        [
            b"%PDF-1.4",
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
            b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj",
            b"trailer<</Root 1 0 R/Size 3>>",
            b"%%EOF",
        ]
    )

    with pytest.raises(ValueError, match="no pages"):
        PDFRenderer(empty)


