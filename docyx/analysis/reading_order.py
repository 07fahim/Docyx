from typing import List, Sequence, Tuple

from docyx.schema.models import Element

# Only these carry a reading position. Containers (`layout_region`, `table`) and
# contained cells are deliberately excluded — numbering a table alongside the
# text inside it interleaves a box with its own contents.
ORDERABLE_TYPES = frozenset({"text"})

# Two pieces of the same visual line overlap vertically by far more than this.
# Below it they are separate lines that merely sit close together.
BAND_OVERLAP = 0.5

# Minimum blank width, in 150 DPI pixels, before a vertical gap counts as a
# column gutter rather than word spacing. A two-column article gutter runs
# 45-55px; an inter-word space at 10pt is about 6px.
MIN_GUTTER = 20.0

# A horizontal cut must be a structural boundary — the space under a figure
# caption, not the leading between two lines. A gap qualifies only if it is
# this much larger than the block's typical gap.
MIN_ROW_GAP = 1.0
ROW_GAP_FACTOR = 1.5

# A gutter is a channel running *down* a block, so each column it produces must
# occupy a real share of the block's height. Without this, any two short lines
# at different heights with clear air between them look like two columns.
MIN_COLUMN_HEIGHT_RATIO = 0.5

# A text column is also *wide*. Table columns are narrow, and cutting on them
# reads a results table downwards instead of across — the classic XY-cut
# failure. Requiring a real share of the block's width rejects those without
# needing to recognise the table.
# 0.25 rather than 0.30 so three-column layouts survive: three equal
# columns plus gutters come to about 0.29 of the block each. Measured on
# the corpus the two thresholds are within 0.1%, so this is free.
MIN_COLUMN_WIDTH_RATIO = 0.25

MAX_DEPTH = 24


class ReadingOrderCalculator:
    """Orders the text layer of a page by recursive XY-cut.

    The page is cut into blocks along blank horizontal or vertical channels,
    recursively, and each block is read in turn. Columns are found by cutting
    vertically; a full-width heading or figure caption blocks a vertical cut,
    which forces a horizontal cut first and so keeps the heading ahead of the
    columns beneath it. This is the layered strategy §10 asks for, in its
    geometric form.

    Cuts are computed over text elements only. Rules and figures routinely span
    the full page width — a single horizontal rule would otherwise bridge the
    gutter and defeat every column cut on the page.

    Within a block that cannot be cut further, elements are grouped into bands
    by vertical overlap and read left to right, so a bold run-in heading stays
    ahead of the sentence it introduces even though its bbox sits a fraction of
    a pixel higher.

    ponytail: geometric only, and left-to-right within a cut. An RTL page will
    read its columns in the wrong order; that needs `direction`, which the
    schema reserves and extraction does not yet populate. Wide-spaced tabular
    text can also cut into columns and read down rather than across, which is
    the classic XY-cut failure — a real table model taking precedence is the
    fix, not more tuning here.
    """

    @staticmethod
    def calculate(
        elements: List[Element],
        min_gutter: float = MIN_GUTTER,
        min_row_gap: float = MIN_ROW_GAP,
    ) -> List[Element]:
        text = [el for el in elements if el.type in ORDERABLE_TYPES]
        others = [el for el in elements if el.type not in ORDERABLE_TYPES]

        ordered = _xy_cut(text, min_gutter, min_row_gap, depth=0)
        for position, element in enumerate(ordered, start=1):
            element.reading_order = position
        for element in others:
            element.reading_order = None

        # Non-text elements carry no reading position; they follow in a stable
        # geometric order so page output stays deterministic.
        return ordered + sorted(others, key=lambda el: (el.geometry.bbox.y, el.geometry.bbox.x))


def _xy_cut(
    elements: List[Element], min_gutter: float, min_row_gap: float, depth: int
) -> List[Element]:
    if len(elements) <= 1 or depth >= MAX_DEPTH:
        return _banded(elements)

    # Columns first. A valid gutter means genuine columns, and a column runs
    # top to bottom before the next one starts — cutting rows first here would
    # produce left, right, left, right instead.
    columns = _split(elements, min_gutter, horizontal=True)
    if len(columns) > 1 and _are_columns(elements, columns):
        return [el for col in columns for el in _xy_cut(col, min_gutter, min_row_gap, depth + 1)]

    # Only the single widest boundary, not every gap. Cutting at all of them
    # shatters a two-column body into strips that each still hold both columns,
    # and no strip can then be cut into columns usefully.
    rows = _widest_row_cut(elements, min_row_gap)
    if rows:
        return [el for row in rows for el in _xy_cut(row, min_gutter, min_row_gap, depth + 1)]

    return _banded(elements)


def _widest_row_cut(elements: List[Element], min_row_gap: float):
    """Split a block in two at its widest horizontal channel, if that channel
    is a structural boundary rather than ordinary line leading.

    Returns None when the block has no such boundary, which is the normal case
    inside a column of body text — banding orders those lines correctly.
    """
    ordered = sorted(elements, key=lambda el: el.geometry.bbox.y)
    gaps = []  # (gap width, index of the element that starts the lower half)
    reach = ordered[0].geometry.bbox.y1
    for index, element in enumerate(ordered[1:], start=1):
        box = element.geometry.bbox
        if box.y - reach > 0:
            gaps.append((box.y - reach, index))
        reach = max(reach, box.y1)

    if not gaps:
        return None

    widest, cut_at = max(gaps)
    typical = _median(sorted(width for width, _ in gaps))
    if widest < max(min_row_gap, typical * ROW_GAP_FACTOR):
        return None
    return [ordered[:cut_at], ordered[cut_at:]]


def _median(values: List[float]) -> float:
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _are_columns(block: List[Element], columns: List[List[Element]]) -> bool:
    """Does every candidate column run down enough of the block to be one?

    Two lines sitting apart horizontally at different heights leave a clear
    vertical channel between them, but they are consecutive lines of one
    column, not two columns. Real columns each span most of the block.
    """
    height = _extent(block, vertical=True)
    width = _extent(block, vertical=False)
    if height <= 0 or width <= 0:
        return False
    return all(
        _extent(col, vertical=True) / height >= MIN_COLUMN_HEIGHT_RATIO
        and _extent(col, vertical=False) / width >= MIN_COLUMN_WIDTH_RATIO
        for col in columns
    )


def _extent(elements: List[Element], vertical: bool) -> float:
    if vertical:
        return max(el.geometry.bbox.y1 for el in elements) - min(
            el.geometry.bbox.y for el in elements
        )
    return max(el.geometry.bbox.x1 for el in elements) - min(
        el.geometry.bbox.x for el in elements
    )


def _split(elements: List[Element], min_gap: float, horizontal: bool) -> List[List[Element]]:
    """Cut a set of elements at every blank channel of at least ``min_gap``.

    Splitting at all qualifying gaps at once — rather than the widest, one at a
    time — keeps recursion shallow on text-dense pages.
    """
    def span(el: Element) -> Tuple[float, float]:
        box = el.geometry.bbox
        return (box.x, box.x1) if horizontal else (box.y, box.y1)

    ordered = sorted(elements, key=lambda el: span(el)[0])
    groups: List[List[Element]] = [[ordered[0]]]
    reach = span(ordered[0])[1]

    for element in ordered[1:]:
        start, end = span(element)
        if start - reach >= min_gap:
            groups.append([element])
        else:
            groups[-1].append(element)
        reach = max(reach, end)
    return groups


def _banded(elements: Sequence[Element]) -> List[Element]:
    """Read a block that resists cutting: line by line, left to right."""
    bands: List[List[Element]] = []
    top = bottom = 0.0

    for element in sorted(elements, key=lambda el: (el.geometry.bbox.y, el.geometry.bbox.x)):
        box = element.geometry.bbox
        if bands and _overlap(box.y, box.y1, top, bottom) >= BAND_OVERLAP:
            bands[-1].append(element)
            top, bottom = min(top, box.y), max(bottom, box.y1)
        else:
            bands.append([element])
            top, bottom = box.y, box.y1

    return [el for band in bands for el in sorted(band, key=lambda e: e.geometry.bbox.x)]


def _overlap(a_top: float, a_bottom: float, b_top: float, b_bottom: float) -> float:
    """Shared vertical extent as a fraction of the shorter of the two."""
    shared = min(a_bottom, b_bottom) - max(a_top, b_top)
    shortest = min(a_bottom - a_top, b_bottom - b_top)
    return shared / shortest if shortest > 0 else 0.0
