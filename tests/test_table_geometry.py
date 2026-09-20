"""Tables from line geometry, with no model.

Precision is tested harder than recall, and deliberately: a missed table costs
a feature, while a two-column page reported as a table silently reorders a page
that was previously correct.
"""

import pytest

from docyx.analysis.table_geometry import find_tables
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Element


#: Cell texts of deliberately varied length, for the reason in `grid_lines`.
CELL_TEXTS = ["7", "a much longer cell", "mid", "quite long here"]


def _write_grid(fitz, path):
    doc = fitz.open()
    page = doc.new_page()
    for row in range(6):
        for column in range(4):
            page.insert_text(
                (72 + column * 120, 100 + row * 20),
                CELL_TEXTS[(row + column) % len(CELL_TEXTS)],
                fontsize=8,
            )
    doc.save(str(path))
    doc.close()


def line(x, y, width, text="x", height=17.0):
    return Element(
        id=f"l{x}_{y}",
        type="text",
        geometry=Geometry(bbox=BoundingBox(x=x, y=y, width=width, height=height)),
        confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
        provenance=Provenance(source=ProvenanceSource.NATIVE_PDF),
        text=text,
    )


def grid_lines(rows, columns, widths=(25, 130, 50, 90)):
    """A plausible table: short cells at regular positions, widths VARYING.

    The variation is not decoration. Real corpus tables measure a width
    coefficient of variation of 0.52-0.75 and two-column prose measures 0.32,
    which is the guard that tells them apart -- so a fixture with uniform
    cell widths is prose wearing a grid, and is correctly rejected.
    """
    return [
        line(150 + column * 160, 100 + row * 30, widths[(row + column) % len(widths)])
        for row in range(rows)
        for column in range(columns)
    ]


def test_a_regular_grid_is_found():
    tables = find_tables(grid_lines(6, 4))

    assert len(tables) == 1
    assert tables[0].type == "table"
    assert len(tables[0].children) == 24
    assert {c.type for c in tables[0].children} == {"table_cell"}


def test_the_grid_positions_are_recovered():
    cells = find_tables(grid_lines(6, 4))[0].children
    positions = {(c.grid.row, c.grid.column) for c in cells}

    assert positions == {(r, c) for r in range(6) for c in range(4)}


def test_a_found_table_is_geometry_inference_not_a_model():
    """Calling this `table_model` would misreport where it came from, and make
    the provenance story unreadable for anyone filtering on it."""
    table = find_tables(grid_lines(6, 4))[0]

    assert table.provenance.source is ProvenanceSource.GEOMETRY_INFERENCE
    assert table.confidence.type is ConfidenceType.DETECTED
    assert table.confidence.value < 1.0


def test_two_column_prose_is_not_a_table():
    """The failure that matters. Every line runs to its column's full measure,
    which is what `MIN_WIDTH_VARIATION` sees and `MAX_FILL` cannot."""
    lines = [
        line(150 + column * 490, 100 + row * 28, 455)
        for row in range(20)
        for column in range(2)
    ]

    assert find_tables(lines) == []


def test_a_running_head_is_not_a_table():
    """`RFC 2616 | HTTP/1.1 | June, 1999` is a real three-column layout."""
    lines = [line(150, 71, 86), line(557, 71, 85), line(960, 71, 90)]

    assert find_tables(lines) == []


def test_scattered_labels_are_not_a_table():
    """A diagram's captions land at arbitrary x, so every one becomes its own
    column. Measured at 18-21 columns on the BERT architecture figure."""
    lines = [
        line(100 + (row * 137 + column * 63) % 900, 100 + row * 30, 20 + column * 11)
        for row in range(5)
        for column in range(6)
    ]

    assert find_tables(lines) == []


def test_a_table_yields_to_an_injected_model(tmp_path):
    """The model had the page image and the first say. Running both would put
    two tables over one block."""
    import fitz

    from docyx.analysis.tables import TableAnalyzer, TableDetection
    from docyx.core.geometry import BoundingBox as Box
    from docyx.pipeline.extractor import DocyxPipeline

    path = tmp_path / "grid.pdf"
    _write_grid(fitz, path)

    detector = lambda image: [TableDetection(  # noqa: E731
        bbox=Box(x=0, y=0, width=10, height=10), score=0.9, cells=[]
    )]
    result = DocyxPipeline(
        table_analyzer=TableAnalyzer(detector=detector), table_geometry=True
    ).process(str(path), "grid").pages[0]

    tables = [el for el in result.elements if el.type == "table"]
    assert len(tables) == 1
    assert tables[0].provenance.source is ProvenanceSource.TABLE_MODEL


def test_it_is_off_unless_asked_for(tmp_path):
    """Region detection is validated on three tables and nine pages with none.
    Enough to offer; not enough to change what every existing caller gets."""
    import fitz

    from docyx.pipeline.extractor import DocyxPipeline

    path = tmp_path / "grid.pdf"
    _write_grid(fitz, path)

    default = DocyxPipeline().process(str(path), "grid").pages[0]
    asked = DocyxPipeline(table_geometry=True).process(str(path), "grid").pages[0]

    assert [el for el in default.elements if el.type == "table"] == []
    assert [el for el in asked.elements if el.type == "table"] != []
