"""XY-cut reading order.

The thresholds here were tuned against real documents (arXiv papers, RFCs, IRS
forms), not chosen a priori — each guard exists because removing it regressed a
measurable case.
"""

from docyx.analysis.reading_order import ReadingOrderCalculator
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Direction, Element

LINE_HEIGHT = 14.0


def _line(id_, x, y, width=400.0):
    return Element(
        id=id_,
        type="text",
        geometry=Geometry(bbox=BoundingBox(x=x, y=y, width=width, height=LINE_HEIGHT)),
        confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
        provenance=Provenance(source=ProvenanceSource.NATIVE_PDF),
        text=id_,
    )


def _order(elements):
    return [el.id for el in ReadingOrderCalculator.calculate(elements) if el.type == "text"]


def test_two_columns_read_down_each_column_in_turn():
    page = []
    for i in range(8):
        page.append(_line(f"L{i}", x=100.0, y=100.0 + i * 20))
        page.append(_line(f"R{i}", x=560.0, y=100.0 + i * 20))

    assert _order(page) == [f"L{i}" for i in range(8)] + [f"R{i}" for i in range(8)]


def test_full_width_heading_stays_above_the_columns_it_introduces():
    """A spanning heading blocks a vertical cut, which forces the horizontal cut
    first — that is what keeps it ahead of both columns."""
    page = [_line("heading", x=100.0, y=50.0, width=860.0)]
    for i in range(6):
        page.append(_line(f"L{i}", x=100.0, y=200.0 + i * 20))
        page.append(_line(f"R{i}", x=560.0, y=200.0 + i * 20))

    assert _order(page) == ["heading"] + [f"L{i}" for i in range(6)] + [f"R{i}" for i in range(6)]


def test_a_narrow_table_is_read_across_its_rows_not_down_its_columns():
    """Found on the GPT-3 paper: a results table was being read column-wise,
    which cost 64 points of reading-order accuracy on one page. Table columns
    are too narrow to be text columns."""
    page = []
    for row in range(4):
        for col in range(5):
            page.append(_line(f"r{row}c{col}", x=100.0 + col * 150, y=100.0 + row * 20, width=90.0))

    order = _order(page)

    assert order[:5] == ["r0c0", "r0c1", "r0c2", "r0c3", "r0c4"]
    assert order[5:10] == ["r1c0", "r1c1", "r1c2", "r1c3", "r1c4"]


def test_two_isolated_lines_are_not_mistaken_for_columns():
    """A gutter is a channel running down a block. Two short lines at different
    heights leave clear air between them but are consecutive lines, not
    columns."""
    assert _order([_line("second", x=10.0, y=140.0, width=50.0),
                   _line("first", x=200.0, y=100.0, width=50.0)]) == ["first", "second"]


def test_single_column_body_text_keeps_its_order():
    page = [_line(f"line{i}", x=100.0, y=100.0 + i * 20) for i in range(10)]
    assert _order(page) == [f"line{i}" for i in range(10)]


def test_run_in_heading_precedes_its_paragraph():
    """Bold has a different ascender, so the heading's bbox sits fractionally
    higher; banding inside an uncuttable block resolves it."""
    page = [
        _line("body", x=200.0, y=100.18, width=300.0),
        _line("heading", x=100.0, y=100.23, width=80.0),
    ]
    assert _order(page) == ["heading", "body"]


def test_containers_are_excluded_but_still_returned():
    text = _line("t", x=100.0, y=100.0)
    table = Element(
        id="table",
        type="table",
        geometry=Geometry(bbox=BoundingBox(x=0.0, y=0.0, width=500.0, height=500.0)),
        confidence=Confidence(value=0.9, type=ConfidenceType.DETECTED),
        provenance=Provenance(source=ProvenanceSource.TABLE_MODEL),
    )

    result = ReadingOrderCalculator.calculate([table, text])

    assert text.reading_order == 1
    assert table.reading_order is None
    assert set(el.id for el in result) == {"t", "table"}


def test_empty_and_single_element_inputs():
    assert ReadingOrderCalculator.calculate([]) == []
    single = _line("only", x=10.0, y=10.0)
    assert [el.reading_order for el in ReadingOrderCalculator.calculate([single])] == [1]


def test_three_columns_still_split():
    """Three columns each take a third of the width, above the 0.30 threshold."""
    page = []
    for i in range(6):
        for c, x in enumerate((60.0, 400.0, 740.0)):
            page.append(_line(f"c{c}l{i}", x=x, y=100.0 + i * 20, width=280.0))

    order = _order(page)
    assert order[:6] == [f"c0l{i}" for i in range(6)]
    assert order[6:12] == [f"c1l{i}" for i in range(6)]


def _vertical(id_, x, y, height):
    """A line of text set vertically — an arXiv stamp down a page's left margin.

    PyMuPDF reports these with a ``dir`` of (0, +/-1), which the extractor
    already turns into ``Direction.TTB``.
    """
    element = _line(id_, x, y, width=26.0)
    element.geometry.bbox.height = height
    element.direction = Direction.TTB
    return element


def test_vertical_margin_stamp_does_not_defeat_column_detection():
    """Measured on arxiv_bert.pdf page 0, where this cost 0.22 similarity.

    A rotated stamp is one line of text whose bbox is as tall as the whole text
    body, so it spans both columns and bridges the gutter — the same failure a
    full-width rule would cause, which is why cuts already exclude non-text
    elements. Vertical text has to come out of the cut geometry for the same
    reason, and is read after the horizontal flow.
    """
    page = [_vertical("stamp", x=20.0, y=100.0, height=260.0)]
    for i in range(8):
        page.append(_line(f"L{i}", x=100.0, y=100.0 + i * 20))
        page.append(_line(f"R{i}", x=560.0, y=100.0 + i * 20))

    expected = [f"L{i}" for i in range(8)] + [f"R{i}" for i in range(8)] + ["stamp"]
    assert _order(page) == expected


def test_four_columns_read_down_each_column_in_turn():
    """MIN_COLUMN_WIDTH_RATIO compared each column against the WHOLE block, so
    it demanded every column be >= 25% of the block width. With N columns each
    is about 1/N, which makes the test unsatisfiable from four columns up: the
    vertical cut was always discarded and the page fell back to banding,
    interleaving every row across all four columns.

    The guard's real intent is that a text column fills its share of the block
    while a table column does not. Measured: real two-column body 0.963, real
    eight-column table 0.417.
    """
    page = []
    for col in range(4):
        for row in range(8):
            page.append(_line(f"C{col}L{row}", x=100.0 + col * 250, y=100.0 + row * 20, width=180.0))

    order = _order(page)

    assert order[:8] == [f"C0L{r}" for r in range(8)]
    assert order[8:16] == [f"C1L{r}" for r in range(8)]
