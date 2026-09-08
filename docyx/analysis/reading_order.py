from typing import List

from docyx.schema.models import Element

# Only these carry a reading position. Containers (`layout_region`, `table`) and
# contained cells are deliberately excluded — numbering a table alongside the
# text inside it interleaves a box with its own contents.
ORDERABLE_TYPES = frozenset({"text"})


class ReadingOrderCalculator:
    """Assigns reading order to the text layer of a page.

    ponytail: raster sort on (y, x) in the canonical 150 DPI system — top to
    bottom, ties left to right. This is NOT an XY-cut: a multi-column page
    interleaves its columns line by line. Upgrade to a real XY-cut (recursive
    projection-profile splitting) when multi-column documents matter.

    Elements are returned sorted geometrically so page output is stable, but
    only ``ORDERABLE_TYPES`` receive a ``reading_order`` number; everything else
    keeps ``None``.
    """

    @staticmethod
    def calculate(elements: List[Element]) -> List[Element]:
        ordered = sorted(
            elements,
            key=lambda el: (el.geometry.bbox.y, el.geometry.bbox.x),
        )
        position = 0
        for element in ordered:
            if element.type in ORDERABLE_TYPES:
                position += 1
                element.reading_order = position
            else:
                element.reading_order = None
        return ordered
