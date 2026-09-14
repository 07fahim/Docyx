import json
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import (
    Confidence,
    ConfidenceType,
    Provenance,
    ProvenanceSource,
)
from docyx.schema.models import Document, Element, Page, PageStatus


def test_element_creation():
    bbox = BoundingBox(x=10.0, y=20.0, width=100.0, height=50.0)
    geom = Geometry(bbox=bbox)
    conf = Confidence(value=1.0, type=ConfidenceType.EXACT)
    prov = Provenance(source=ProvenanceSource.NATIVE_PDF)

    elem = Element(
        id="elem_001",
        type="paragraph",
        geometry=geom,
        confidence=conf,
        provenance=prov,
        text="Hello world",
    )

    assert elem.id == "elem_001"
    assert elem.type == "paragraph"
    assert elem.text == "Hello world"
    assert elem.geometry.bbox.x == 10.0
    assert elem.confidence.type == ConfidenceType.EXACT
    assert elem.provenance.source == ProvenanceSource.NATIVE_PDF


def test_page_and_document_serialization():
    bbox = BoundingBox(x=0.0, y=0.0, width=50.0, height=50.0)
    geom = Geometry(bbox=bbox)
    conf = Confidence(value=0.9, type=ConfidenceType.DETECTED)
    prov = Provenance(source=ProvenanceSource.LAYOUT_MODEL, engine="YOLOv8")

    elem = Element(
        id="elem_001",
        type="title",
        geometry=geom,
        confidence=conf,
        provenance=prov,
        text="Title Text",
    )

    diag_elem = Element(
        id="diag_001",
        type="image",
        geometry=geom,
        confidence=Confidence(value=0.5, type=ConfidenceType.INFERRED),
        provenance=Provenance(source=ProvenanceSource.VISUAL_INFERENCE),
    )

    page = Page(
        page_number=1,
        status=PageStatus.OK,
        width=1200,
        height=1600,
        source_type="born_digital",
        elements=[elem],
        diagnostic_elements=[diag_elem],
    )

    doc = Document(document_id="doc_123", pages=[page])

    assert doc.schema_version == "1.4"
    assert len(doc.pages) == 1
    assert doc.pages[0].status == PageStatus.OK
    assert len(doc.pages[0].elements) == 1
    assert len(doc.pages[0].diagnostic_elements) == 1

    # Test dict export
    doc_dict = doc.model_dump()
    assert doc_dict["schema_version"] == "1.4"
    assert doc_dict["document_id"] == "doc_123"
    assert "diagnostic_elements" in doc_dict["pages"][0]
    assert doc_dict["pages"][0]["diagnostic_elements"][0]["id"] == "diag_001"

    # Test JSON export
    json_str = doc.model_dump_json()
    data = json.loads(json_str)
    assert data["schema_version"] == "1.4"
    assert data["pages"][0]["diagnostic_elements"][0]["id"] == "diag_001"


def test_page_status_values():
    assert PageStatus.OK == "ok"
    assert PageStatus.PARTIAL == "partial"
    assert PageStatus.FAILED == "failed"


# --- human edits (§11) -----------------------------------------------------


def _element(text="machine text", source=ProvenanceSource.NATIVE_PDF):
    return Element(
        id="e1",
        type="text",
        geometry=Geometry(bbox=BoundingBox(x=0, y=0, width=10, height=10)),
        confidence=Confidence(value=0.6, type=ConfidenceType.INFERRED),
        provenance=Provenance(source=source, engine="tesseract"),
        text=text,
    )


def test_an_edit_preserves_what_the_machine_said():
    element = _element().edit_text("corrected text")

    assert element.text == "corrected text"
    assert element.provenance.original_text == "machine text"
    assert element.provenance.modified_by_user is True


def test_editing_twice_keeps_the_ORIGINAL_not_the_first_correction():
    """The obvious implementation overwrites original_text on every edit, which
    silently destroys the machine's value on the second keystroke-save."""
    element = _element().edit_text("first try").edit_text("second try")

    assert element.text == "second try"
    assert element.provenance.original_text == "machine text"


def test_an_edit_does_not_rewrite_where_the_element_came_from():
    """`source` is a fact about history. Overwriting it with `manual` would
    make a corrected OCR line indistinguishable from one a human drew from
    nothing — the distinction the whole annotation workflow rests on."""
    element = _element(source=ProvenanceSource.OCR).edit_text("corrected")

    assert element.provenance.source is ProvenanceSource.OCR
    assert element.provenance.engine == "tesseract"


def test_an_edited_element_becomes_exact():
    """A person reading the rendered page outranks any extractor, and on a
    damaged text layer they are the only authority available."""
    element = _element().edit_text("corrected")

    assert element.confidence.type is ConfidenceType.EXACT
    assert element.confidence.value == 1.0


def test_an_untouched_element_carries_no_edit_marks():
    element = _element()

    assert element.provenance.modified_by_user is False
    assert element.provenance.original_text is None
