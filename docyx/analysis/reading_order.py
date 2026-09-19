"""Reading order by recursive XY-cut, with banding as the fallback.

See CLAUDE.md "Reading order" for the rationale behind each threshold and the
measurements that set them. Re-run scripts/measure_reading_order.py before
changing any of them.
"""

from typing import List, Optional, Sequence, Tuple

from docyx.core.metadata import ProvenanceSource
from docyx.schema.models import Direction, Element

#: Types that carry a reading position, including the semantic roles a layout
#: model assigns to a line.
TEXT_ROLES = frozenset(
    {
        "text",
        "title",
        "section_header",
        "caption",
        "footnote",
        "formula",
        "list_item",
        "page_header",
        "page_footer",
    }
)

#: Type-only view of the same set. is_orderable() is the real test:
#: this admits layout REGIONS, which share these type names.
ORDERABLE_TYPES = TEXT_ROLES

#: Vertical overlap above which two elements belong to the same visual line.
BAND_OVERLAP = 0.5

#: Blank width, in 150 DPI pixels, before a vertical gap counts as a gutter.
MIN_GUTTER = 20.0

#: A row cut must exceed both this and ROW_GAP_FACTOR x the block's typical gap.
MIN_ROW_GAP = 1.0
ROW_GAP_FACTOR = 1.5

#: Each column must span this fraction of the block's height. Do not lower it
#: to capture floats: it costs the pages currently scoring 1.000.
MIN_COLUMN_HEIGHT_RATIO = 0.5

#: A column must satisfy one of these two width tests, not both. Fill
#: generalises to any column count; share is more forgiving of wide gutters.
MIN_COLUMN_FILL = 0.70
MIN_COLUMN_WIDTH_RATIO = 0.25

MAX_DEPTH = 24


def is_orderable(element: Element) -> bool:
    """Whether the element takes a position in the reading sequence."""
    # Provenance, not type: a layout region and a line can both be "caption".
    if element.provenance.source is ProvenanceSource.LAYOUT_MODEL:
        return False
    return element.type in TEXT_ROLES


class ReadingOrderCalculator:
    """Orders a page's text by recursive XY-cut."""

    @staticmethod
    def calculate(
        elements: List[Element],
        min_gutter: float = MIN_GUTTER,
        min_row_gap: float = MIN_ROW_GAP,
    ) -> List[Element]:
        orderable = [el for el in elements if is_orderable(el)]
        others = [el for el in elements if not is_orderable(el)]

        # Vertically-set text is excluded from the cut geometry: its bbox spans
        # the page and would bridge every gutter. It still gets a position.
        flow = [el for el in orderable if el.direction is not Direction.TTB]
        vertical = [el for el in orderable if el.direction is Direction.TTB]

        ordered = _xy_cut(flow, min_gutter, min_row_gap, depth=0) + _geometric(vertical)
        for position, element in enumerate(ordered, start=1):
            element.reading_order = position
        for element in others:
            element.reading_order = None

        return ordered + _geometric(others)


def _geometric(elements: List[Element]) -> List[Element]:
    """Top-to-bottom, left-to-right."""
    return sorted(elements, key=lambda el: (el.geometry.bbox.y, el.geometry.bbox.x))


def _xy_cut(
    elements: List[Element], min_gutter: float, min_row_gap: float, depth: int
) -> List[Element]:
    if len(elements) <= 1 or depth >= MAX_DEPTH:
        return _banded(elements)

    # Columns before rows: a column runs top to bottom before the next starts.
    columns = _split(elements, min_gutter, horizontal=True)
    if len(columns) > 1 and _are_columns(elements, columns):
        if _is_rtl(elements):
            columns.reverse()
        return [
            el for col in columns for el in _xy_cut(col, min_gutter, min_row_gap, depth + 1)
        ]

    rows = _widest_row_cut(elements, min_row_gap)
    if rows:
        return [el for row in rows for el in _xy_cut(row, min_gutter, min_row_gap, depth + 1)]

    return _banded(elements)


def _is_rtl(elements: List[Element]) -> bool:
    """Whether the block reads right to left, by majority of its elements."""
    rtl = sum(1 for el in elements if el.direction is Direction.RTL)
    return rtl * 2 > len(elements)


def _widest_row_cut(
    elements: List[Element], min_row_gap: float
) -> Optional[List[List[Element]]]:
    """Split at the widest horizontal channel, if it is a structural boundary.

    Returns None inside ordinary body text, where banding orders the lines.
    """
    ordered = sorted(elements, key=lambda el: el.geometry.bbox.y)
    gaps: List[Tuple[float, int]] = []
    reach = ordered[0].geometry.bbox.y1
    for index, element in enumerate(ordered[1:], start=1):
        box = element.geometry.bbox
        if box.y - reach > 0:
            gaps.append((box.y - reach, index))
        reach = max(reach, box.y1)

    if not gaps:
        return None

    # Only the widest gap. Cutting at every gap shatters a two-column body into
    # strips that each still contain both columns.
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
    """Whether the candidate columns are real columns rather than stray gaps."""
    height = _extent(block, vertical=True)
    width = _extent(block, vertical=False)
    if height <= 0 or width <= 0:
        return False
    slot = width / len(columns)

    def wide_enough(col: List[Element]) -> bool:
        span = _extent(col, vertical=False)
        return span / width >= MIN_COLUMN_WIDTH_RATIO or span / slot >= MIN_COLUMN_FILL

    return all(
        _extent(col, vertical=True) / height >= MIN_COLUMN_HEIGHT_RATIO and wide_enough(col)
        for col in columns
    )


def _extent(elements: List[Element], vertical: bool) -> float:
    boxes = [el.geometry.bbox for el in elements]
    if vertical:
        return max(b.y1 for b in boxes) - min(b.y for b in boxes)
    return max(b.x1 for b in boxes) - min(b.x for b in boxes)


def _split(elements: List[Element], min_gap: float, horizontal: bool) -> List[List[Element]]:
    """Cut at every blank channel of at least `min_gap`."""

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
    """Read a block that resists cutting, line by line in reading direction."""
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

    # reverse= matters: sorting RTL bands left to right reverses every row.
    rtl = _is_rtl(elements)
    return [
        el
        for band in bands
        for el in sorted(band, key=lambda e: e.geometry.bbox.x, reverse=rtl)
    ]


def _overlap(a_top: float, a_bottom: float, b_top: float, b_bottom: float) -> float:
    """Shared vertical extent as a fraction of the shorter of the two."""
    shared = min(a_bottom, b_bottom) - max(a_top, b_top)
    shortest = min(a_bottom - a_top, b_bottom - b_top)
    return shared / shortest if shortest > 0 else 0.0
