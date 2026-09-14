"""OCR through the analyzer seam.

A fake detector everywhere, so these run without tesseract installed — the
same seam-testing pattern the other three analyzers use. What is pinned here
is the *contract*: where recognised text is allowed to appear, what confidence
and provenance it carries, and that nothing changes when no detector is given.
"""

import fitz
import pytest

from docyx.analysis.ocr import OCRAnalyzer, OCRLine
from docyx.core.geometry import BoundingBox
from docyx.pipeline.extractor import DocyxPipeline
from docyx.schema.models import Direction, PageStatus


@pytest.fixture
def scanned_pdf(tmp_path):
    """Pixels, no text layer — fails the gate without OCR."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=fitz.open().new_page().get_pixmap().tobytes("png"))
    path = tmp_path / "scanned.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def text_pdf(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "Real text in the document.", fontsize=11)
    path = tmp_path / "text.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def fake_detector(lines=(("Recognised line one", 0.92), ("second line", 0.81))):
    def detect(image_bytes):
        return [
            OCRLine(bbox=BoundingBox(x=50.0, y=100.0 + 40 * i, width=400.0, height=30.0),
                    text=text, score=score)
            for i, (text, score) in enumerate(lines)
        ]

    detect.engine = "fake-ocr"
    return detect


def analyzer(**kwargs):
    return OCRAnalyzer(detector=fake_detector(), **kwargs)


def test_without_a_detector_the_page_still_fails(scanned_pdf):
    """The default pipeline must behave exactly as it did before OCR existed."""
    page = DocyxPipeline().process(scanned_pdf, document_id="d").pages[0]

    assert page.status is PageStatus.FAILED
    assert page.errors[0].code == "NO_TEXT_LAYER"
    assert page.elements == []


def test_recognised_text_rescues_a_scanned_page(scanned_pdf):
    page = DocyxPipeline(ocr_analyzer=analyzer()).process(scanned_pdf, document_id="d").pages[0]

    assert page.status is PageStatus.PARTIAL, "recognised text is never `ok`"
    assert page.source_type == "scanned"
    assert not page.errors, "a page that produced content must not carry an error"
    assert {w.code for w in page.warnings} == {"OCR_TEXT"}
    assert [el.text for el in page.elements] == ["Recognised line one", "second line"]


def test_recognised_text_is_inferred_and_attributed_to_the_engine(scanned_pdf):
    """The two reserved schema values activate here and nowhere else."""
    page = DocyxPipeline(ocr_analyzer=analyzer()).process(scanned_pdf, document_id="d").pages[0]
    element = page.elements[0]

    assert element.confidence.type.value == "inferred"
    assert element.confidence.value == 0.92
    assert element.provenance.source.value == "ocr"
    assert element.provenance.engine == "fake-ocr", "the detector, not the analyzer"
    assert element.typography is None, "a recogniser reports characters, not fonts"


def test_native_text_is_never_re_read_from_pixels(text_pdf):
    """OCR on a page that has a text layer would downgrade exact to inferred."""
    page = DocyxPipeline(ocr_analyzer=analyzer()).process(text_pdf, document_id="d").pages[0]

    assert page.status is PageStatus.OK
    assert all(el.confidence.type.value == "exact" for el in page.elements)
    assert all(el.provenance.source.value == "native_pdf" for el in page.elements)


def test_recognised_lines_are_put_in_reading_order(scanned_pdf):
    page = DocyxPipeline(ocr_analyzer=analyzer()).process(scanned_pdf, document_id="d").pages[0]

    assert [el.reading_order for el in page.elements] == [1, 2]


def test_a_broken_engine_costs_the_page_not_the_document(scanned_pdf):
    def explode(image_bytes):
        raise RuntimeError("tesseract not on PATH")

    page = (
        DocyxPipeline(ocr_analyzer=OCRAnalyzer(detector=explode))
        .process(scanned_pdf, document_id="d")
        .pages[0]
    )

    assert page.status is PageStatus.FAILED
    assert page.errors[0].code == "NO_TEXT_LAYER"
    assert [w.code for w in page.warnings] == ["STAGE_FAILED"]


def test_low_confidence_lines_are_dropped():
    """Page noise recognised as a one-character word derails the lines near it."""
    detector = fake_detector((("good line", 0.9), ("|", 0.11)))
    elements = OCRAnalyzer(detector=detector, min_confidence=0.4).analyze(b"")

    assert [el.text for el in elements] == ["good line"]


@pytest.fixture
def misordered_pdf(tmp_path):
    """A page whose text layer is present and readable but stored in visual
    order — mirrored delimiters are what that actually looks like: ']1[' where
    the document reads '[1]'."""
    doc = fitz.open()
    doc.new_page().insert_text(
        (72, 100), "مرحبا ]1[ )2020(", fontsize=11,
        fontfile=r"C:\Windows\Fonts\arial.ttf", fontname="ar",
    )
    path = tmp_path / "rtl.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def test_repair_is_off_unless_asked(misordered_pdf):
    """Replacing exact text with inferred text is never the obvious call."""
    page = DocyxPipeline(ocr_analyzer=analyzer()).process(misordered_pdf, "d").pages[0]

    assert [w.code for w in page.warnings] == ["RTL_VISUAL_ORDER"]
    assert all(el.provenance.source.value == "native_pdf" for el in page.elements)


def test_repair_swaps_in_ocr_and_keeps_the_native_text(misordered_pdf):
    page = (
        DocyxPipeline(ocr_analyzer=analyzer(), ocr_repair=True)
        .process(misordered_pdf, "d")
        .pages[0]
    )

    assert {w.code for w in page.warnings} == {"RTL_VISUAL_ORDER", "OCR_REPAIRED"}
    assert page.status is PageStatus.PARTIAL, "it was already partial from the warning"
    assert [el.provenance.source.value for el in page.elements] == ["ocr", "ocr"]
    # Nothing is discarded: the native text is exact, only its order is wrong.
    assert page.diagnostic_elements
    assert all(el.confidence.type.value == "exact" for el in page.diagnostic_elements)


def test_a_clean_page_is_never_repaired(text_pdf):
    page = (
        DocyxPipeline(ocr_analyzer=analyzer(), ocr_repair=True)
        .process(text_pdf, "d")
        .pages[0]
    )

    assert page.status is PageStatus.OK
    assert all(el.provenance.source.value == "native_pdf" for el in page.elements)


def test_repair_covers_only_the_reordering_defects():
    """TEXT_LAYER_SUSPECT means the CHARACTERS are wrong, not their order. OCR
    might help or might not, so it is not a case this can decide unattended."""
    from docyx.pipeline.extractor import REPAIRABLE_CODES

    assert REPAIRABLE_CODES == {"RTL_VISUAL_ORDER", "COMBINING_MARK_ORDER"}


def test_direction_is_read_from_the_recognised_characters():
    detector = fake_detector((("مرحبا بالعالم", 0.9), ("hello world", 0.9), ("2021", 0.9)))
    elements = OCRAnalyzer(detector=detector).analyze(b"")

    assert [el.direction for el in elements] == [
        Direction.RTL,
        Direction.LTR,
        Direction.UNKNOWN,
    ]
