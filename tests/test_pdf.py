import os
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

def test_pipeline_gate_failure(tmp_path):
    # Our stub gate fails if 'fail' is in the file path
    doc = fitz.open()
    doc.new_page()
    pdf_path = tmp_path / "fail_doc.pdf"
    doc.save(str(pdf_path))
    doc.close()
    
    pipeline = DocyxPipeline()
    doc_model = pipeline.process(str(pdf_path), document_id="doc_fail")
    
    page = doc_model.pages[0]
    assert page.status == PageStatus.FAILED
    assert len(page.elements) == 0
    assert len(page.errors) == 1
    assert "NO_TEXT_LAYER" in page.errors[0]
