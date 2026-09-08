from typing import List

from docyx.schema.models import Element

# Only these carry a reading position. Containers (`layout_region`, `table`) and
# contained cells are deliberately excluded — numbering a table alongside the
# text inside it interleaves a box with its own contents.
ORDERABLE_TYPES = frozenset({"text"})

# Two pieces of the same visual line overlap vertically by far more than this.
# Below it they are separate lines that merely sit close together.
BAND_OVERLAP = 0.5


class ReadingOrderCalculator:
    """Assigns reading order to the text layer of a page.

    Sorting on raw (y, x) is too literal: a bold run-in heading like
    "**Encoder:** The encoder is composed of..." shares a visual line with the
    text it introduces, but its bbox sits a fraction of a pixel off because a
    bold face has a different ascender. Exact comparison then orders the
    heading *after* the sentence it begins.

    So elements are first grouped into horizontal bands by vertical overlap,
    then ordered left to right within each band.

    ponytail: banding, not an XY-cut. This orders a page as a sequence of
    lines, which is right for single-column text and still wrong for multiple
    columns — a two-column page interleaves, because both columns occupy the
    same bands. Fixing that needs real column detection (recursive
    projection-profile splitting); see the xfail in tests/test_markdown.py.

    Elements are returned sorted so page output is stable, but only
    ``ORDERABLE_TYPES`` receive a ``reading_order`` number; everything else
    keeps ``None``.
    """

    @staticmethod
    def calculate(elements: List[Element]) -> List[Element]:
        ordered: List[Element] = []
        for band in _bands(elements):
            ordered.extend(sorted(band, key=lambda el: el.geometry.bbox.x))

        position = 0
        for element in ordered:
            if element.type in ORDERABLE_TYPES:
                position += 1
                element.reading_order = position
            else:
                element.reading_order = None
        return ordered


def _bands(elements: List[Element]) -> List[List[Element]]:
    """Group elements that share a visual line, top to bottom."""
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
    return bands


def _overlap(a_top: float, a_bottom: float, b_top: float, b_bottom: float) -> float:
    """Shared vertical extent as a fraction of the shorter of the two."""
    shared = min(a_bottom, b_bottom) - max(a_top, b_top)
    shortest = min(a_bottom - a_top, b_bottom - b_top)
    return shared / shortest if shortest > 0 else 0.0
