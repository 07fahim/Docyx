"""Recover table structure from text-line geometry, with no model.

`TableAnalyzer`'s detector seam takes a page image, which is the right shape
for a vision model and the wrong one for this: Docyx already knows where every
line sits to the pixel, and throwing that away to re-find it in pixels is
giving up the one advantage a PDF tool has over a scanner.

The measurement that motivated this is in `scripts/measure_tables.py`. Given a
table's lines, clustering their x and y positions recovers the grid at least as
well as Table Transformer does (mean adjacency F1 0.766 against 0.731 over
three labelled tables). So the model was never buying the structure -- it was
buying the part this module has to do for itself, which is deciding *which*
lines form a table at all.

**Finding the region is the whole difficulty, and the enemy is a two-column
paper.** A page of prose set in two columns has lines at repeating x positions
and looks superficially like a table; so does a running head, a title page's
author block, a block of displayed equations, and a diagram's scattered
labels. Seven guards separate them, and EVERY ONE is here because removing it
produced a false table on a real corpus page:

    MIN_COLUMNS         3+ column positions. Two-column prose has exactly 2.
    MAX_COLUMNS         12 or fewer. A diagram's labels resolve to 18-21.
    MIN_LINES           12+. `RFC 2616 | HTTP/1.1 | June, 1999` is a real
                        three-column layout, and so is every running foot.
    MIN_ROWS            3+, or any two lines side by side qualify.
    MIN_DENSITY         A real grid is mostly full.
    MAX_FILL            A cell is short; prose fills its column.
    MIN/MAX_WIDTH_VARIATION   The one that finally worked. See below.

**No single test does this**, and the two that looked sufficient both failed
on a page found later. `MAX_FILL` cannot separate a two-column abstract at
0.84 from a real table at 0.80. Width VARIATION can, and cleanly: prose lines
all run to the same measure (0.32) while table cells do not (0.52-0.75), and
equations and diagram labels vary more than either (0.98, 1.12). So a table is
bounded on both sides -- more varied than prose, less arbitrary than a figure.

`scripts/measure_table_regions.py` grades this, and it grades PRECISION first.
A missed table costs a feature; a two-column page reported as a table corrupts
the reading order of a page that was previously correct, and does it silently.
Currently 1.00 coverage on three labelled tables and zero false tables across
nine pages that have none.

**Off unless asked for** (`--tables`, `DocyxPipeline(table_geometry=True)`).
Three labelled tables is enough evidence to offer this and not enough to
change what every existing caller gets. Each time the adversarial page list
grew, a new false-positive class appeared -- equations were the fourth -- so
the honest reading is that more classes remain unfound.

The output is `geometry_inference`, not `table_model`: it is measured from
geometry, the same claim `VisualAnalyzer` makes, and calling it a model would
misreport where it came from.
"""

from typing import Dict, List, Optional, Sequence, Tuple

from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Element, GridPosition

#: A table needs at least this many distinct column positions. Two-column
#: prose has exactly 2, which is why the bound is 3.
MIN_COLUMNS = 3

#: And not more than this. Real tables here run 5 to 8 columns; the BERT
#: architecture figure's scattered labels resolve to 18-21, because a diagram's
#: captions land at arbitrary x positions and every one becomes a column.
MAX_COLUMNS = 12

#: Coefficient of variation of line widths. **Prose lines are all the same
#: width** -- that is what "justified to the measure" means -- while table
#: cells are not. Measured: real tables 0.52 / 0.67 / 0.75 against 0.32 for a
#: two-column abstract. This is the guard that `fill` could not be: the
#: abstract fills 0.84 of its pitch and a real table fills 0.80, so those two
#: are inseparable by width alone and trivially separable by its variation.
MIN_WIDTH_VARIATION = 0.40

#: And the other side of it. Too uniform is prose; too scattered is not a grid
#: at all. Measured: displayed equations 0.98 and a diagram's labels 1.12,
#: against real tables at 0.52-0.75. A table's cells vary in width, but within
#: a range -- that boundedness is what makes it a table.
MAX_WIDTH_VARIATION = 0.85

#: And at least this many rows, or two lines side by side become a table.
MIN_ROWS = 3

#: Share of the column pitch a line may fill before the block reads as prose
#: rather than as cells. Body text runs to its column's full measure; a cell
#: almost never does. Measured: real tables 0.46 / 0.60 / 0.80, against 0.97
#: for a title page's author block and 1.04 for two-column prose under a
#: section heading. 0.9 sits in that gap and is the single most discriminating
#: number here -- it is what separates a grid of cells from a grid of prose.
MAX_FILL = 0.9

#: A table this small is page furniture. `RFC 2616 | HTTP/1.1 | June, 1999` is
#: a genuine three-column single-row layout, and so is every running foot.
MIN_LINES = 12

#: Occupied cells over rows x columns. A real grid is mostly full; a scatter
#: of lines that merely happen to start at repeating x positions is not.
MIN_DENSITY = 0.35

#: Share of the lines inside a candidate's bounding box that must belong to it.
#: A real table's box holds its own cells and nothing else. The runs that
#: survived every other test were sub-runs of a two-column page whose box then
#: spanned the gutter and swallowed the column beside them -- which is exactly
#: the failure this catches, and the only one that still mattered.
MIN_PURITY = 0.95

#: Two lines belong to the same row band when their vertical extents overlap
#: by this share of the shorter one.
ROW_OVERLAP = 0.4

#: Column starts closer than this share of the median line height are the
#: same column.
COLUMN_TOLERANCE = 1.5

#: A running head or foot sits inside this share of the page from the edge.
#: `arxiv_gpt3` p7's real table header starts at 9.5%, so 8% keeps it out.
FURNITURE_BAND = 0.08

#: And is separated from the body by at least this multiple of the median gap
#: between row bands. Measured: `rfc2616`'s running head is 3.1x and its foot
#: 3.9x, against 1.0x for `nasa_budget` p88's table heading and 1.4x for
#: `arxiv_gpt3` p7's. Both bounds must hold, so a table that merely starts
#: high on the page keeps its header row.
FURNITURE_GAP = 2.0

ENGINE = "table-geometry-v1"


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0.0


def _row_bands(lines: List[Element]) -> List[List[int]]:
    """Group line indices into visual rows by vertical overlap."""
    order = sorted(range(len(lines)), key=lambda i: lines[i].geometry.bbox.y)
    bands: List[List[int]] = []
    for index in order:
        box = lines[index].geometry.bbox
        for band in bands:
            other = lines[band[-1]].geometry.bbox
            top, bottom = max(box.y, other.y), min(
                box.y + box.height, other.y + other.height
            )
            shorter = min(box.height, other.height) or 1.0
            if (bottom - top) / shorter >= ROW_OVERLAP:
                band.append(index)
                break
        else:
            bands.append([index])
    return bands


def _strip_furniture(lines: List[Element], bands: List[List[int]],
                     page_height: float) -> List[List[int]]:
    """Drop a running head or foot before looking for tables.

    `RFC 2616 | HTTP/1.1 | June, 1999` is three short aligned cells at the top
    of every page -- a perfect false anchor. It establishes three columns, and
    the prose beneath is then pulled into the box with it. Five of the
    fourteen false positives in the precision sample began exactly this way.

    Removing it is correct regardless of what it does for detection: a running
    head belongs to no table on the page.
    """
    if len(bands) < MIN_ROWS + 2 or page_height <= 0:
        return bands

    tops = [min(lines[i].geometry.bbox.y for i in band) for band in bands]
    gaps = [b - a for a, b in zip(tops, tops[1:])]
    median = _median(gaps)
    if median <= 0:
        return bands

    first, last = 0, len(bands)
    if tops[0] < page_height * FURNITURE_BAND and gaps[0] >= median * FURNITURE_GAP:
        first = 1
    if (tops[-1] > page_height * (1 - FURNITURE_BAND)
            and gaps[-1] >= median * FURNITURE_GAP):
        last -= 1
    return bands[first:last]


def _columns(lines: List[Element], indices: Sequence[int], tolerance: float) -> List[float]:
    """Distinct left edges, merged when closer together than `tolerance`."""
    starts = sorted(lines[i].geometry.bbox.x for i in indices)
    columns: List[float] = []
    for start in starts:
        if not columns or start - columns[-1] > tolerance:
            columns.append(start)
    return columns


def _pitch(columns: Sequence[float], right: float) -> float:
    """Median distance between column starts -- the width a cell may occupy."""
    edges = list(columns) + [right]
    return _median([b - a for a, b in zip(edges, edges[1:])]) or 1.0


def _column_of(x: float, columns: Sequence[float]) -> int:
    """Index of the nearest column start at or before `x`."""
    best = 0
    for index, start in enumerate(columns):
        if x + 1.0 >= start:
            best = index
    return best


def _candidate(lines: List[Element], bands: List[List[int]]) -> bool:
    """Does this run of row bands read as a table rather than as prose?"""
    indices = [i for band in bands for i in band]
    if len(bands) < MIN_ROWS or len(indices) < MIN_LINES:
        return False
    height = _median([lines[i].geometry.bbox.height for i in indices]) or 1.0
    columns = _columns(lines, indices, height * COLUMN_TOLERANCE)
    if not MIN_COLUMNS <= len(columns) <= MAX_COLUMNS:
        return False

    widths = [lines[i].geometry.bbox.width for i in indices]
    mean = sum(widths) / len(widths)
    variation = (sum((w - mean) ** 2 for w in widths) / len(widths)) ** 0.5 / (mean or 1.0)
    if not MIN_WIDTH_VARIATION <= variation <= MAX_WIDTH_VARIATION:
        return False

    right = max(
        lines[i].geometry.bbox.x + lines[i].geometry.bbox.width for i in indices
    )
    # Prose fills its column; a cell does not.
    fill = _median([lines[i].geometry.bbox.width for i in indices]) / _pitch(columns, right)
    if fill > MAX_FILL:
        return False

    # No single test separates a table from a page that merely has repeating
    # left edges. Measured over three labelled tables and six pages with none:
    #
    #             lines   density   fill
    #   tables    21-72   0.45-1.00  0.46-0.80
    #   not       5-38    0.17-0.56  0.22-2.04
    #
    # Every axis overlaps on its own -- a title page's author block beats two
    # of the tables on line count -- and together they separate with margin.
    occupied = len(_grid(lines, bands))
    if occupied / (len(bands) * len(columns)) < MIN_DENSITY:
        return False

    return _purity(lines, indices) >= MIN_PURITY


def _purity(lines: List[Element], indices: Sequence[int]) -> float:
    """Share of the lines inside the candidate's box that are part of it."""
    box = _hull(lines, indices)
    inside = [
        index for index, line in enumerate(lines)
        if box.contains(
            line.geometry.bbox.x + line.geometry.bbox.width / 2,
            line.geometry.bbox.y + line.geometry.bbox.height / 2,
        )
    ]
    return len(set(indices) & set(inside)) / len(inside) if inside else 0.0


def _grid(lines: List[Element], bands: List[List[int]]) -> Dict[Tuple[int, int], List[int]]:
    indices = [i for band in bands for i in band]
    height = _median([lines[i].geometry.bbox.height for i in indices]) or 1.0
    columns = _columns(lines, indices, height * COLUMN_TOLERANCE)

    cells: Dict[Tuple[int, int], List[int]] = {}
    for row, band in enumerate(bands):
        for index in band:
            column = _column_of(lines[index].geometry.bbox.x, columns)
            cells.setdefault((row, column), []).append(index)
    return cells


def _hull(lines: List[Element], indices: Sequence[int]) -> BoundingBox:
    boxes = [lines[i].geometry.bbox for i in indices]
    x = min(b.x for b in boxes)
    y = min(b.y for b in boxes)
    return BoundingBox(
        x=x,
        y=y,
        width=max(b.x + b.width for b in boxes) - x,
        height=max(b.y + b.height for b in boxes) - y,
    )


def _element(element_id: str, bbox: BoundingBox, type_: str,
             grid: Optional[GridPosition] = None) -> Element:
    return Element(
        id=element_id,
        type=type_,
        geometry=Geometry(bbox=bbox),
        # Geometry alone can say a grid is there; it cannot be certain.
        confidence=Confidence(value=0.6, type=ConfidenceType.DETECTED),
        provenance=Provenance(
            source=ProvenanceSource.GEOMETRY_INFERENCE,
            engine=ENGINE,
            raw_confidence=0.6,
        ),
        grid=grid,
    )


def find_tables(lines: List[Element], page_num: int = 0,
                page_height: float = 0.0) -> List[Element]:
    """`table` elements with `table_cell` children, inferred from line positions.

    Takes the page's extracted text lines and returns detections in the same
    shape `TableAnalyzer` produces, so everything downstream -- cell text,
    reading order, the bundle export -- works unchanged.
    """
    lines = [el for el in lines if el.type == "text" and el.geometry.bbox.width > 0]
    if len(lines) < MIN_COLUMNS * MIN_ROWS:
        return []

    bands = _strip_furniture(lines, _row_bands(lines), page_height)
    tables: List[Element] = []

    # Grow a run of consecutive bands for as long as it still reads as a table.
    start = 0
    while start < len(bands):
        end = start
        best: Optional[int] = None
        while end < len(bands):
            if _candidate(lines, bands[start:end + 1]):
                best = end + 1
            elif best is not None and end + 1 - start > len(bands[start:best]) + 2:
                # Two bands past the last good run: stop rather than swallow
                # the paragraph under the table.
                break
            end += 1

        if best is None:
            start += 1
            continue

        run = bands[start:best]
        indices = [i for band in run for i in band]
        table_id = f"page{page_num + 1}_geotable_{len(tables)}"
        cells = _grid(lines, run)
        tables.append(
            _element(table_id, _hull(lines, indices), "table")
        )
        tables[-1].children = [
            _element(
                f"{table_id}_r{row}_c{column}",
                _hull(lines, members),
                "table_cell",
                GridPosition(row=row, column=column, row_span=1, column_span=1),
            )
            for (row, column), members in sorted(cells.items())
        ]
        start = best

    return tables
