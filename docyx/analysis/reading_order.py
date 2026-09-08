from typing import List

from docyx.schema.models import Element


class ReadingOrderCalculator:
    """Assigns reading order to elements using an XY-cut geometric sort.

    Elements are ordered top-to-bottom, then left-to-right, based on their
    bounding boxes in the canonical 150 DPI coordinate system. The sort is
    deterministic and only meaningful for elements on pages whose text layer
    passed the gate.
    """

    @staticmethod
    def calculate(elements: List[Element]) -> List[Element]:
        ordered = sorted(
            elements,
            key=lambda el: (el.geometry.bbox.y, el.geometry.bbox.x),
        )
        for idx, element in enumerate(ordered, start=1):
            element.reading_order = idx
        return ordered