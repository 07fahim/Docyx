import fitz
import pytest

from docyx.analysis.layout import LayoutAnalyzer
from docyx.analysis.reading_order import ReadingOrderCalculator
from docyx.analysis.tables import CellDetection, TableAnalyzer, TableDetection
from docyx.analysis.visual import VisualAnalyzer, VisualDetection
from docyx.core.geometry import BoundingBox
from docyx.core.metadata import ConfidenceType, ProvenanceSource
from docyx.pipeline.extractor import DocyxPipeline
from docyx.schema.models import Element, PageStatus


NO_VISUALS = VisualAnalyzer(detector=lambda image_bytes: [])


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
    detector = lambda image_bytes: [(BoundingBox(x=0.0, y=0.0, width=100.0, height=50.0), 0.87)]
    analyzer = LayoutAnalyzer(detector=detector)

    elements = analyzer.analyze(b"fake-png-bytes", page_num=0)

    assert len(elements) == 1
    el = elements[0]
    assert el.type == "layout_region"
    assert el.confidence.type == ConfidenceType.DETECTED
    assert el.confidence.value == 0.87
    assert el.provenance.raw_confidence == 0.87
    assert el.provenance.source == ProvenanceSource.LAYOUT_MODEL
    assert el.id == "page1_layout_0"


def test_table_analyzer_detected_confidence():
    detector = lambda image_bytes: [
        TableDetection(BoundingBox(x=0.0, y=0.0, width=200.0, height=120.0), 0.91),
        TableDetection(BoundingBox(x=0.0, y=200.0, width=100.0, height=80.0), 0.42),
    ]
    analyzer = TableAnalyzer(detector=detector)

    elements = analyzer.analyze(b"fake-png-bytes", page_num=0)

    assert len(elements) == 2
    assert all(el.type == "table" for el in elements)
    assert all(el.confidence.type == ConfidenceType.DETECTED for el in elements)
    assert [el.confidence.value for el in elements] == [0.91, 0.42]
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
    pdf_path = tmp_path / "blank_doc.pdf"
    doc.save(str(pdf_path))
    doc.close()

    layout_detector = lambda image_bytes: [(BoundingBox(x=0.0, y=0.0, width=100.0, height=50.0), 0.8)]
    table_detector = lambda image_bytes: [
        TableDetection(BoundingBox(x=0.0, y=100.0, width=50.0, height=50.0), 0.7)
    ]

    pipeline = DocyxPipeline(
        layout_analyzer=LayoutAnalyzer(detector=layout_detector),
        table_analyzer=TableAnalyzer(detector=table_detector),
        visual_analyzer=NO_VISUALS,
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
    layout_detector = lambda image_bytes: [(BoundingBox(x=0.0, y=0.0, width=100.0, height=50.0), 0.8)]
    table_detector = lambda image_bytes: [
        TableDetection(BoundingBox(x=0.0, y=100.0, width=50.0, height=50.0), 0.7)
    ]

    pipeline = DocyxPipeline(
        layout_analyzer=LayoutAnalyzer(detector=layout_detector),
        table_analyzer=TableAnalyzer(detector=table_detector),
        visual_analyzer=NO_VISUALS,
    )
    doc_model = pipeline.process(sample_pdf_path, document_id="doc_ok")

    page = doc_model.pages[0]
    assert page.status == PageStatus.OK
    assert len(page.elements) >= 3
    types = {el.type for el in page.elements}
    assert "text" in types
    assert "layout_region" in types
    assert "table" in types
    text_order = [el.reading_order for el in page.elements if el.type == "text"]
    assert text_order == sorted(text_order)
    assert None not in text_order
    # Containers are excluded: numbering a table alongside its own contents
    # would interleave a box with the text inside it.
    assert all(
        el.reading_order is None
        for el in page.elements
        if el.type in {"layout_region", "table"}
    )
    assert page.diagnostic_elements == []

def test_reading_order_skips_containers():
    text = _text_element(x=10.0, y=10.0)
    table = Element(
        id="t",
        type="table",
        geometry=text.geometry.model_copy(deep=True),
        confidence=text.confidence,
        provenance=text.provenance,
    )
    later_text = _text_element(x=10.0, y=900.0)

    ordered = ReadingOrderCalculator.calculate([later_text, table, text])

    assert [el.reading_order for el in ordered if el.type == "text"] == [1, 2]
    assert next(el for el in ordered if el.type == "table").reading_order is None


def test_table_analyzer_builds_cell_grid():
    detector = lambda image_bytes: [
        TableDetection(
            bbox=BoundingBox(x=0.0, y=0.0, width=200.0, height=100.0),
            score=0.9,
            cells=[
                CellDetection(BoundingBox(x=0.0, y=0.0, width=100.0, height=50.0), row=0, column=0),
                CellDetection(BoundingBox(x=100.0, y=0.0, width=100.0, height=50.0), row=0, column=1),
                CellDetection(
                    BoundingBox(x=0.0, y=50.0, width=200.0, height=50.0),
                    row=1,
                    column=0,
                    column_span=2,
                ),
            ],
        )
    ]

    table = TableAnalyzer(detector=detector).analyze(b"png", page_num=0)[0]

    assert len(table.children) == 3
    assert all(c.type == "table_cell" for c in table.children)
    assert [(c.grid.row, c.grid.column) for c in table.children] == [(0, 0), (0, 1), (1, 0)]
    assert table.children[2].grid.column_span == 2
    assert [c.id for c in table.children] == [
        "page1_table_0_r0_c0",
        "page1_table_0_r0_c1",
        "page1_table_0_r1_c0",
    ]
    assert detector(b"")[0].row_count == 2
    assert detector(b"")[0].column_count == 2


def test_table_without_recovered_structure_has_no_children():
    detector = lambda image_bytes: [
        TableDetection(bbox=BoundingBox(x=0.0, y=0.0, width=10.0, height=10.0), score=0.5)
    ]
    table = TableAnalyzer(detector=detector).analyze(b"png")[0]
    assert table.children == []


def test_visual_analyzer_uses_geometry_inference_provenance():
    detector = lambda image_bytes: [
        VisualDetection(BoundingBox(x=0.0, y=0.0, width=500.0, height=2.0), "rule", 0.5)
    ]
    el = VisualAnalyzer(detector=detector).analyze(b"png", page_num=0)[0]

    assert el.type == "rule"
    assert el.provenance.source == ProvenanceSource.GEOMETRY_INFERENCE
    assert el.confidence.type == ConfidenceType.DETECTED
    assert el.id == "page1_visual_0"
