from typing import Callable, List, Optional

from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Element

Detector = Callable[[bytes], List[BoundingBox]]


class TableAnalyzer:
    """Detects table regions from a rendered page image.

    The detector callable performs the actual table inference. When no detector
    is supplied, a deterministic heuristic stub is used (no detections) so the
    pipeline is testable without model weights.
    """

    ENGINE = "table-heuristic-v1"

    def __init__(self, detector: Optional[Detector] = None):
        self._detector = detector

    def analyze(self, image_bytes: bytes, page_num: int = 0) -> List[Element]:
        boxes = self._detector(image_bytes) if self._detector else self._heuristic(image_bytes)
        elements = []
        for idx, box in enumerate(boxes):
            elements.append(
                Element(
                    id=f"page{page_num + 1}_table_{idx}",
                    type="table",
                    geometry=Geometry(bbox=box),
                    confidence=Confidence(value=0.0, type=ConfidenceType.DETECTED),
                    provenance=Provenance(source=ProvenanceSource.TABLE_MODEL, engine=self.ENGINE),
                )
            )
        return elements

    @staticmethod
    def _heuristic(image_bytes: bytes) -> List[BoundingBox]:
        return []