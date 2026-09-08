import pytest
import fitz
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
    assert ids == ["page1_b0_l0_s0", "page1_b1_l0_s0"]


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
    assert any("layout_detection failed" in w for w in page.warnings)
    assert page.errors == []
