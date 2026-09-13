"""Writing direction (§13), read from the PDF rather than inferred.

Per-element *language* is deliberately not implemented: PDFs carry no such
field, so it would require detection and could not honestly be `exact`.
Document-level /Lang exists in tagged PDFs but is a separate feature.
"""

import os

import fitz
import pytest

from docyx.analysis.reading_order import ReadingOrderCalculator
from docyx.core.constants import SCALE
from docyx.pipeline.extractor import DocyxPipeline
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


def test_arabic_is_rtl_even_when_the_pdf_reports_no_bidi_level():
    """Measured on a real Arabic PDF (.corpus/wiki_ar.pdf): every span reports
    ``bidi=0``, because the generator baked visual order into the glyph stream
    instead of preserving logical order plus embedding levels. That is normal
    for PDF — it is a presentation format.

    Trusting ``bidi`` therefore classified all 74 elements on the page as LTR
    and left the RTL column-ordering path dead on real documents. The script's
    own Unicode bidi category is the reliable signal.
    """
    assert _direction({"dir": (1.0, 0.0)}, [_span("متوسط العمر", bidi=0)]) is Direction.RTL


def test_bengali_is_ltr_not_merely_non_latin():
    """Guards the obvious over-correction: 'not Latin' is not 'right to left'.
    Measured on .corpus/wiki_bn.pdf, where 0 of 100 lines are RTL."""
    assert _direction({"dir": (1.0, 0.0)}, [_span("বৃহত্তম নগরী", bidi=0)]) is Direction.LTR


def test_digits_and_punctuation_alone_do_not_decide_direction():
    """Bidi-neutral characters carry no direction, so a line of them is unknown
    rather than defaulting to LTR."""
    assert _direction({"dir": (1.0, 0.0)}, [_span("(2020) [5] 42", bidi=0)]) is Direction.UNKNOWN


ARABIC_FONT = r"C:\Windows\Fonts\arial.ttf"


@pytest.mark.skipif(
    not os.path.exists(ARABIC_FONT), reason="needs a system font with Arabic coverage"
)
def test_rtl_columns_read_right_to_left_end_to_end(tmp_path):
    """The RTL column path, exercised through a real PDF rather than fixtures.

    Every other RTL test builds Element objects directly, so extraction and
    ordering were never verified together on a file — and the one real Arabic
    document available (.corpus/wiki_ar.pdf) cannot serve as ground truth: its
    bidi-neutral runs are stored in visual order, and labelling its reading
    order needs someone who reads Arabic.

    Constructing the page instead makes the ground truth definitional: the
    right column is written first, so it must be read first. PyMuPDF's base-14
    fonts replace Arabic with dots, hence the system font.
    """
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    # Long enough that each column is ~40% of the block width. A shorter line
    # gave 0.239 and was rejected by MIN_COLUMN_WIDTH_RATIO (0.25) — the guard
    # that stops narrow table columns being read downwards. Real two-column
    # bodies sit near 0.45, so the fixture has to be realistic to test columns.
    arabic = "مرحبا بالعالم اليوم مرحبا بالعالم اليوم"
    # Right column written first: on an RTL page it must also be read first.
    for i in range(8):
        page.insert_text((330, 120 + i * 30), arabic, fontfile=ARABIC_FONT,
                         fontname="ar", fontsize=11)
        page.insert_text((60, 120 + i * 30), arabic, fontfile=ARABIC_FONT,
                         fontname="ar", fontsize=11)
    pdf = tmp_path / "rtl_columns.pdf"
    doc.save(str(pdf))
    doc.close()

    result = DocyxPipeline().process(str(pdf), "rtl")
    text = [el for el in result.pages[0].elements if el.type == "text"]

    assert text, "no text extracted"
    assert all(el.direction is Direction.RTL for el in text), (
        f"expected all RTL, got {set(el.direction for el in text)}"
    )

    ordered = sorted(text, key=lambda el: el.reading_order)
    midpoint = 295 * SCALE
    first_half = [el.geometry.bbox.x > midpoint for el in ordered[: len(ordered) // 2]]
    assert all(first_half), (
        "right-hand column must be read first on an RTL page; got x positions "
        f"{[round(el.geometry.bbox.x) for el in ordered]}"
    )


def test_rtl_bands_read_right_to_left():
    """Columns respected `direction`; bands did not.

    A block that resists cutting falls back to banding, and banding sorted each
    band left to right unconditionally. On an RTL page that reverses every row
    that holds more than one element. Found on .corpus/wiki_ar.pdf p6, where a
    figure caption sits beside the body text: Docyx scored 0.893 adjacency
    against hand-labelled truth, BELOW the naive baseline's 0.911.
    """
    # One band: a caption on the right, body text on the left. RTL reads the
    # right-hand element first.
    # x ranges must OVERLAP, or a gutter exists and the column path handles it
    # (which already respects direction). Banding is the fallback for blocks
    # that cannot be cut, and it is the path that ignored direction.
    near = _line("near", x=300.0, y=563.0, direction=Direction.RTL)
    far = _line("far", x=100.0, y=565.0, direction=Direction.RTL)

    order = [el.id for el in ReadingOrderCalculator.calculate([far, near])]
    assert order == ["near", "far"], "RTL band must read right to left"


def test_ltr_bands_still_read_left_to_right():
    right = _line("right", x=300.0, y=563.0, direction=Direction.LTR)
    left = _line("left", x=100.0, y=565.0, direction=Direction.LTR)

    order = [el.id for el in ReadingOrderCalculator.calculate([right, left])]
    assert order == ["left", "right"]
