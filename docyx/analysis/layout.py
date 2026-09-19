from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple, Union

from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Element

#: The DocLayNet vocabulary. Data rather than an enum so a detector with its
#: own labels can map into it.
LAYOUT_LABELS = frozenset(
    {
        "caption",
        "footnote",
        "formula",
        "list_item",
        "page_footer",
        "page_header",
        "picture",
        "section_header",
        "table",
        "text_region",
        "title",
    }
)

#: What an unlabelled or unrecognised detection becomes.
UNTYPED = "layout_region"


@dataclass
class LayoutDetection:
    """One detected region and its class."""

    bbox: BoundingBox
    score: float
    label: str = UNTYPED


#: Bare `(bbox, score)` tuples remain accepted; they arrive as UNTYPED.
Detection = Union[LayoutDetection, Tuple[BoundingBox, float]]
Detector = Callable[[bytes], Sequence[Detection]]


def _normalise(detection: Detection) -> LayoutDetection:
    if isinstance(detection, LayoutDetection):
        return detection
    box, score = detection
    return LayoutDetection(bbox=box, score=score)


class LayoutAnalyzer:
    """Detects layout regions from a rendered page image.

    No detector ships, so no region is produced by default.
    """

    ENGINE = "layout-heuristic-v1"

    def __init__(self, detector: Optional[Detector] = None):
        self._detector = detector

    def _engine(self) -> str:
        """The detector's own engine name, or the built-in heuristic's."""
        return getattr(self._detector, "engine", None) or self.ENGINE

    def analyze(self, image_bytes: bytes, page_num: int = 0) -> List[Element]:
        raw = self._detector(image_bytes) if self._detector else self._heuristic(image_bytes)
        elements = []
        for idx, detection in enumerate(raw):
            found = _normalise(detection)
            # Unknown labels become UNTYPED: `type` is a bare string, so a
            # detector typo would otherwise create a silent new type.
            label = found.label if found.label in LAYOUT_LABELS else UNTYPED
            elements.append(
                Element(
                    id=f"page{page_num + 1}_layout_{idx}",
                    type=label,
                    geometry=Geometry(bbox=found.bbox),
                    confidence=Confidence(value=found.score, type=ConfidenceType.DETECTED),
                    provenance=Provenance(
                        source=ProvenanceSource.LAYOUT_MODEL,
                        engine=self._engine(),
                        raw_confidence=found.score,
                    ),
                )
            )
        return elements

    @staticmethod
    def _heuristic(image_bytes: bytes) -> List[LayoutDetection]:
        return []
