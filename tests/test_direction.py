"""Writing direction (§13), read from the PDF rather than inferred.

Per-element *language* is deliberately not implemented: PDFs carry no such
field, so it would require detection and could not honestly be `exact`.
Document-level /Lang exists in tagged PDFs but is a separate feature.
"""

import fitz

from docyx.analysis.reading_order import ReadingOrderCalculator
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.pdf.text_extractor import NativeTextExtractor, _direction
from docyx.schema.models import Direction, Element


def _span(text, bidi=0):
    return {"text": text, "size": 10.0, "font": "F", "flags": 0, "color": 0, "bidi": bidi}


def test_horizontal_text_is_ltr():
    assert _direction({"dir": (1.0, 0.0)}, [_span("hello")]) is Direction.LTR


def test_odd_bidi_level_is_rtl():
    """Bidi embedding levels are odd for right-to-left runs."""
    assert _direction({"dir": (1.0, 0.0)}, [_span("שלום", bidi=1)]) is Direction.RTL


def test_vertical_text_is_ttb():
    assert _direction({"dir": (0.0, -1.0)}, [_span("vertical")]) is Direction.TTB
    assert _direction({"dir": (0.0, 1.0)}, [_span("vertical")]) is Direction.TTB


def test_degenerate_direction_vector_is_unknown():
    assert _direction({"dir": (0.0, 0.0)}, [_span("x")]) is Direction.UNKNOWN


def test_direction_uses_the_dominant_span():
    """A single stray RTL mark must not flip a whole line of English."""
    line = {"dir": (1.0, 0.0)}
    spans = [_span("א", bidi=1), _span("a long run of english text")]
    assert _direction(line, spans) is Direction.LTR


def test_direction_is_populated_end_to_end(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "Ordinary text", fontsize=11)
    pdf = tmp_path / "d.pdf"
    doc.save(str(pdf))
    doc.close()

    src = fitz.open(str(pdf))
    elements = NativeTextExtractor(src).extract_page(0)
    src.close()

    assert elements[0].direction is Direction.LTR


def test_rotated_text_reports_ttb_end_to_end(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((300, 300), "Sideways", fontsize=11, rotate=90)
    pdf = tmp_path / "r.pdf"
    doc.save(str(pdf))
    doc.close()

    src = fitz.open(str(pdf))
    elements = NativeTextExtractor(src).extract_page(0)
    src.close()

    assert elements[0].direction is Direction.TTB


def _line(id_, x, y, direction):
    return Element(
        id=id_,
        type="text",
        geometry=Geometry(bbox=BoundingBox(x=x, y=y, width=400.0, height=14.0)),
        confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
        provenance=Provenance(source=ProvenanceSource.NATIVE_PDF),
        text=id_,
        direction=direction,
    )


def test_rtl_columns_are_read_right_to_left():
    """The reason direction is extracted at all: an RTL page begins at the
    right-hand column, and geometry alone cannot know that."""
    page = []
    for i in range(6):
        page.append(_line(f"near{i}", x=560.0, y=100.0 + i * 20, direction=Direction.RTL))
        page.append(_line(f"far{i}", x=100.0, y=100.0 + i * 20, direction=Direction.RTL))

    order = [el.id for el in ReadingOrderCalculator.calculate(page)]

    assert order == [f"near{i}" for i in range(6)] + [f"far{i}" for i in range(6)]


def test_ltr_columns_are_unaffected():
    page = []
    for i in range(6):
        page.append(_line(f"L{i}", x=100.0, y=100.0 + i * 20, direction=Direction.LTR))
        page.append(_line(f"R{i}", x=560.0, y=100.0 + i * 20, direction=Direction.LTR))

    order = [el.id for el in ReadingOrderCalculator.calculate(page)]

    assert order == [f"L{i}" for i in range(6)] + [f"R{i}" for i in range(6)]


def test_a_minority_of_rtl_lines_does_not_flip_the_page():
    page = []
    for i in range(6):
        page.append(_line(f"L{i}", x=100.0, y=100.0 + i * 20, direction=Direction.LTR))
        page.append(_line(f"R{i}", x=560.0, y=100.0 + i * 20, direction=Direction.LTR))
    page.append(_line("stray", x=100.0, y=220.0, direction=Direction.RTL))

    order = [el.id for el in ReadingOrderCalculator.calculate(page)]

    assert order[0] == "L0"
