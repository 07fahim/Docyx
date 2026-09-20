"""Table Transformer detector.

Split deliberately:

* The grid-building logic is tested with the model faked out, so it runs
  everywhere and pins the behaviour that matters — rows x columns becoming a
  cell grid in page coordinates.
* The real model is exercised only when the optional stack is installed, and
  skipped otherwise, so the core suite stays fast and dependency-free.
"""

import io

import pytest

from docyx.analysis.tables import TableAnalyzer, TableDetection

models = pytest.importorskip
transformers_available = True
try:  # pragma: no cover - environment dependent
    import torch  # noqa: F401
    import transformers  # noqa: F401
    from PIL import Image  # noqa: F401
except ImportError:  # pragma: no cover
    transformers_available = False

needs_models = pytest.mark.skipif(
    not transformers_available, reason="optional model stack not installed"
)


@needs_models
def test_cells_are_the_intersection_of_row_and_column_strips():
    """TATR returns rows and columns as overlapping strips, never cells. The
    grid is their intersection, and this is where that is built."""
    from docyx.analysis.detectors.table_transformer import TableTransformerDetector

    detector = TableTransformerDetector.__new__(TableTransformerDetector)
    detector.device = "cpu"
    detector.detection_threshold = 0.7
    detector.structure_threshold = 0.6
    detector._detection = None
    detector._structure = None

    class _Crop:
        width, height = 200, 100

        def crop(self, _box):
            return self

    # two row strips and three column strips -> a 2x3 grid
    strips = [
        ("table column header", [0.0, 0.0, 200.0, 40.0], 0.95),
        ("table row", [0.0, 40.0, 200.0, 90.0], 0.9),
        ("table column", [0.0, 0.0, 60.0, 100.0], 0.9),
        ("table column", [60.0, 0.0, 130.0, 100.0], 0.9),
        ("table column", [130.0, 0.0, 200.0, 100.0], 0.9),
    ]
    detector._run = lambda models, image, threshold: strips
    detector._structure_model = lambda: None

    cells = detector._cells(_Crop(), [0.0, 0.0, 200.0, 100.0])

    assert len(cells) == 6
    assert sorted({c.row for c in cells}) == [0, 1]
    assert sorted({c.column for c in cells}) == [0, 1, 2]
    # header strip sorts above the body row
    top_row = [c for c in cells if c.row == 0]
    assert all(c.bbox.y < 40.0 + 12.0 for c in top_row)


@needs_models
def test_no_rows_or_no_columns_yields_no_cells():
    """A table whose structure could not be resolved must report an empty grid,
    not a fabricated one — TableDetection tolerates that by design."""
    from docyx.analysis.detectors.table_transformer import TableTransformerDetector

    detector = TableTransformerDetector.__new__(TableTransformerDetector)
    detector.device = "cpu"
    detector.structure_threshold = 0.6
    detector._structure = None

    class _Crop:
        width, height = 200, 100

        def crop(self, _box):
            return self

    detector._run = lambda models, image, threshold: [("table row", [0, 0, 200, 50], 0.9)]
    detector._structure_model = lambda: None

    assert detector._cells(_Crop(), [0.0, 0.0, 200.0, 100.0]) == []


def test_detector_output_flows_through_the_analyzer_unchanged():
    """The seam contract: whatever produces TableDetections, TableAnalyzer turns
    them into a table element with table_cell children carrying grid positions.
    Runs without the model stack."""
    from docyx.analysis.tables import CellDetection
    from docyx.core.geometry import BoundingBox

    detection = TableDetection(
        bbox=BoundingBox(x=10.0, y=20.0, width=200.0, height=100.0),
        score=0.93,
        cells=[
            CellDetection(BoundingBox(x=10.0, y=20.0, width=100.0, height=50.0), row=0, column=0),
            CellDetection(BoundingBox(x=110.0, y=20.0, width=100.0, height=50.0), row=0, column=1),
        ],
    )

    element = TableAnalyzer(detector=lambda _: [detection]).analyze(b"png")[0]

    assert element.type == "table"
    assert element.confidence.value == 0.93
    assert [c.grid.column for c in element.children] == [0, 1]
    assert detection.row_count == 1 and detection.column_count == 2


def test_missing_optional_stack_raises_a_useful_error(monkeypatch):
    """If someone constructs the detector without the extra installed, the
    error must say what to install rather than surfacing a bare ImportError."""
    import docyx.analysis.detectors.table_transformer as mod

    def _boom():
        raise ImportError("no torch")

    monkeypatch.setattr(mod, "_require_deps", _boom)
    with pytest.raises(ImportError, match="requirements-models.txt|no torch"):
        mod.TableTransformerDetector()


def test_provenance_names_the_detector_not_the_heuristic():
    """A model's output must not be attributed to the heuristic it replaced —
    provenance is the basis of the swappability claim (26.11)."""
    from docyx.core.geometry import BoundingBox

    class _Detector:
        engine = "microsoft/table-transformer"

        def __call__(self, _image):
            return [TableDetection(bbox=BoundingBox(x=0.0, y=0.0, width=10.0, height=10.0), score=0.9)]

    element = TableAnalyzer(detector=_Detector()).analyze(b"png")[0]
    assert element.provenance.engine == "microsoft/table-transformer"


def test_a_detector_without_an_engine_falls_back_to_the_builtin_name():
    from docyx.core.geometry import BoundingBox

    detector = lambda _: [
        TableDetection(bbox=BoundingBox(x=0.0, y=0.0, width=10.0, height=10.0), score=0.9)
    ]
    element = TableAnalyzer(detector=detector).analyze(b"png")[0]
    assert element.provenance.engine == TableAnalyzer.ENGINE


def test_cell_text_comes_from_the_native_layer_by_position(tmp_path):
    """Cells are geometry only until native text is joined to them by position.
    No pixels are read as text - the no-OCR contract holds."""
    import fitz

    from docyx.analysis.tables import CellDetection
    from docyx.core.geometry import BoundingBox
    from docyx.pipeline.extractor import DocyxPipeline

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Widget", fontsize=11)
    page.insert_text((300, 100), "42", fontsize=11)
    pdf = tmp_path / "t.pdf"
    doc.save(str(pdf))
    doc.close()

    # cells placed over each of the two words, in 150 DPI space
    detector = lambda _: [
        TableDetection(
            bbox=BoundingBox(x=100.0, y=170.0, width=600.0, height=60.0),
            score=0.9,
            cells=[
                CellDetection(BoundingBox(x=100.0, y=170.0, width=300.0, height=60.0), row=0, column=0),
                CellDetection(BoundingBox(x=400.0, y=170.0, width=300.0, height=60.0), row=0, column=1),
            ],
        )
    ]
    result = DocyxPipeline(table_analyzer=TableAnalyzer(detector=detector)).process(str(pdf), "d")
    table = [e for e in result.pages[0].elements if e.type == "table"][0]

    assert table.children[0].text == "Widget"
    assert table.children[1].text == "42"
    # and the lines remain in the page's own element list
    assert any(e.type == "text" and e.text == "Widget" for e in result.pages[0].elements)


def test_cells_with_no_text_beneath_them_stay_empty():
    from docyx.analysis.tables import CellDetection
    from docyx.core.geometry import BoundingBox
    from docyx.pipeline.extractor import _populate_cell_text

    detection = TableDetection(
        bbox=BoundingBox(x=0.0, y=0.0, width=100.0, height=100.0),
        score=0.9,
        cells=[CellDetection(BoundingBox(x=0.0, y=0.0, width=50.0, height=50.0), row=0, column=0)],
    )
    table = TableAnalyzer(detector=lambda _: [detection]).analyze(b"png")[0]

    _populate_cell_text([table])

    assert table.children[0].text is None


# --- the structure scorer's own metric --------------------------------------
# Tested without weights: the metric is arithmetic over line sets, and a
# scorer nobody can check is worth no more than the score it prints.


def _grid(rows):
    """{(row, column): frozenset} from a list-of-lists of line-index lists."""
    return {(r, c): frozenset(cell)
            for r, row in enumerate(rows)
            for c, cell in enumerate(row) if cell}


def test_a_perfect_grid_scores_one():
    from scripts.measure_tables import score

    truth = _grid([[[0], [1]], [[2], [3]]])

    cell, adjacency = score(truth, dict(truth))

    assert cell == (1.0, 1.0, 1.0)
    assert adjacency == (1.0, 1.0, 1.0)


def test_a_missing_header_row_costs_that_row_and_not_the_whole_table():
    """The Table Transformer drops the header row on every corpus table, and a
    metric that renumbered everything below it would call a one-row error a
    total failure."""
    from scripts.measure_tables import score

    truth = _grid([[[0], [1]], [[2], [3]], [[4], [5]]])
    predicted = _grid([[[2], [3]], [[4], [5]]])

    cell, adjacency = score(truth, predicted)

    assert cell[0] == 1.0                 # nothing it reported was wrong
    assert cell[1] == pytest.approx(4 / 6)
    assert adjacency[0] == 1.0            # and the rows it kept are still adjacent
    assert adjacency[2] > 0.6


def test_a_transposed_table_keeps_its_cells_and_loses_its_structure():
    """Why both numbers are quoted. Every cell is individually correct, so
    `cell` is perfect; nothing sits beside what it used to, so `adj` is zero."""
    from scripts.measure_tables import score

    truth = _grid([[[0], [1]], [[2], [3]]])
    transposed = _grid([[[0], [2]], [[1], [3]]])

    cell, adjacency = score(truth, transposed)

    assert cell == (1.0, 1.0, 1.0)
    assert adjacency[2] == 0.0


def test_a_cell_split_in_two_is_penalised_on_both_sides():
    """The NASA failure: a multi-line cell broken up. It loses the truth cell
    (recall) and adds two that match nothing (precision)."""
    from scripts.measure_tables import score

    truth = _grid([[[0, 1], [2]]])
    split = _grid([[[0], [2]], [[1], None]])

    cell, _ = score(truth, split)

    assert cell[0] < 1.0
    assert cell[1] < 1.0


def test_geometry_baseline_recovers_a_clean_grid():
    """The baseline has to be able to win, or the comparison proves nothing --
    and on a clean grid it does win, which is the corpus result."""
    from docyx.core.geometry import BoundingBox, Geometry
    from scripts.measure_tables import geometry_grid

    class Line:
        def __init__(self, x, y):
            self.geometry = Geometry(bbox=BoundingBox(x=x, y=y, width=30, height=10))

    lines = [Line(x, y) for y in (100, 130) for x in (50, 200)]

    grid = geometry_grid(lines, set(range(4)))

    assert sorted(grid) == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert grid[(0, 0)] == frozenset({0})
    assert grid[(1, 1)] == frozenset({3})
