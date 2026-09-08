import fitz
import pytest

from docyx.analysis.layout import LayoutAnalyzer
from docyx.analysis.reading_order import ReadingOrderCalculator
from docyx.analysis.tables import TableAnalyzer
from docyx.core.geometry import BoundingBox
from docyx.core.metadata import ConfidenceType, ProvenanceSource
from docyx.pipeline.extractor import DocyxPipeline
from docyx.schema.models import Element, PageStatus


def _text_element(x: float, y: float) -> Element:
    from docyx.core.geometry import Geometry
    from docyx.core.metadata import Confidence, Provenance

    return Element(
        id=f"text_{x}_{y}",
        type="text",
        geometry=Geometry(bbox=BoundingBox(x=x, y=y, width=10.0, height=10.0)),
        confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
        provenance=Provenance(source=ProvenanceSource.NATIVE_PDF),
    )


def test_layout_analyzer_detected_confidence():
    detector = lambda image_bytes: [BoundingBox(x=0.0, y=0.0, width=100.0, height=50.0)]
    analyzer = LayoutAnalyzer(detector=detector)

    elements = analyzer.analyze(b"fake-png-bytes", page_num=0)

    assert len(elements) == 1
    el = elements[0]
    assert el.type == "layout_region"
    assert el.confidence.type == ConfidenceType.DETECTED
    assert el.provenance.source == ProvenanceSource.LAYOUT_MODEL
    assert el.id == "page1_layout_0"


def test_table_analyzer_detected_confidence():
    detector = lambda image_bytes: [
        BoundingBox(x=0.0, y=0.0, width=200.0, height=120.0),
        BoundingBox(x=0.0, y=200.0, width=100.0, height=80.0),
    ]
    analyzer = TableAnalyzer(detector=detector)

    elements = analyzer.analyze(b"fake-png-bytes", page_num=0)

    assert len(elements) == 2
    assert all(el.type == "table" for el in elements)
    assert all(el.confidence.type == ConfidenceType.DETECTED for el in elements)
    assert all(el.provenance.source == ProvenanceSource.TABLE_MODEL for el in elements)


def test_reading_order_xy_sort():
    bottom_right = _text_element(x=500.0, y=400.0)
    top_left = _text_element(x=10.0, y=10.0)
    top_right = _text_element(x=600.0, y=10.0)

    ordered = ReadingOrderCalculator.calculate([bottom_right, top_left, top_right])

    assert [el.id for el in ordered] == [top_left.id, top_right.id, bottom_right.id]
    assert [el.reading_order for el in ordered] == [1, 2, 3]


@pytest.fixture
def sample_pdf_path(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Hello, World!")
    pdf_path = tmp_path / "sample.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return str(pdf_path)


def test_pipeline_routes_visual_elements_to_diagnostics(tmp_path):
    doc = fitz.open()
    doc.new_page()
    pdf_path = tmp_path / "fail_doc.pdf"
    doc.save(str(pdf_path))
    doc.close()

    layout_detector = lambda image_bytes: [BoundingBox(x=0.0, y=0.0, width=100.0, height=50.0)]
    table_detector = lambda image_bytes: [BoundingBox(x=0.0, y=100.0, width=50.0, height=50.0)]

    pipeline = DocyxPipeline(
        layout_analyzer=LayoutAnalyzer(detector=layout_detector),
        table_analyzer=TableAnalyzer(detector=table_detector),
    )
    doc_model = pipeline.process(str(pdf_path), document_id="doc_fail")

    page = doc_model.pages[0]
    assert page.status == PageStatus.FAILED
    assert page.elements == []
    assert len(page.diagnostic_elements) == 2
    assert page.diagnostic_elements[0].type == "layout_region"
    assert page.diagnostic_elements[1].type == "table"
    assert all(el.reading_order is None for el in page.diagnostic_elements)


def test_pipeline_combines_elements_with_reading_order(sample_pdf_path):
    layout_detector = lambda image_bytes: [BoundingBox(x=0.0, y=0.0, width=100.0, height=50.0)]
    table_detector = lambda image_bytes: [BoundingBox(x=0.0, y=100.0, width=50.0, height=50.0)]

    pipeline = DocyxPipeline(
        layout_analyzer=LayoutAnalyzer(detector=layout_detector),
        table_analyzer=TableAnalyzer(detector=table_detector),
    )
    doc_model = pipeline.process(sample_pdf_path, document_id="doc_ok")

    page = doc_model.pages[0]
    assert page.status == PageStatus.OK
    assert len(page.elements) >= 3
    types = {el.type for el in page.elements}
    assert "text" in types
    assert "layout_region" in types
    assert "table" in types
    assert all(el.reading_order is not None for el in page.elements)
    assert [el.reading_order for el in page.elements] == sorted(
        el.reading_order for el in page.elements
    )
    assert page.diagnostic_elements == []