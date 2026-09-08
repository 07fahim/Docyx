from typing import Callable, List, Optional

from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Element

Detector = Callable[[bytes], List[BoundingBox]]


class LayoutAnalyzer:
    """Detects layout regions (heading, figure, paragraph, etc.) from a rendered page image.

    The detector callable performs the actual ML inference. When no detector is
    supplied, a deterministic heuristic stub is used so the pipeline is fully
    testable without downloading heavy model weights.
    """

    ENGINE = "layout-heuristic-v1"

    def __init__(self, detector: Optional[Detector] = None):
        self._detector = detector

    def analyze(self, image_bytes: bytes, page_num: int = 0) -> List[Element]:
        boxes = self._detector(image_bytes) if self._detector else self._heuristic(image_bytes)
        elements = []
        for idx, box in enumerate(boxes):
            elements.append(
                Element(
                    id=f"page{page_num + 1}_layout_{idx}",
                    type="layout_region",
                    geometry=Geometry(bbox=box),
                    confidence=Confidence(value=0.0, type=ConfidenceType.DETECTED),
                    provenance=Provenance(source=ProvenanceSource.LAYOUT_MODEL, engine=self.ENGINE),
                )
            )
        return elements

    @staticmethod
    def _heuristic(image_bytes: bytes) -> List[BoundingBox]:
        return []