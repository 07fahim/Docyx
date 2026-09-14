from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple, Union

from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Element

#: The DocLayNet vocabulary, which is what every published layout model emits
#: and what `.corpus/truth/` was labelled against. Kept as data rather than an
#: enum so a detector with a different vocabulary can map into it without this
#: module needing to know about it.
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

#: What an unlabelled or unrecognised detection becomes. This was the ONLY type
#: this module could ever produce before a label could travel through the seam.
UNTYPED = "layout_region"


@dataclass
class LayoutDetection:
    """One detected region and what the model thinks it is.

    `label` is the point of a layout model. Without it every region is an
    untyped rectangle, and the caller cannot tell a caption from a paragraph —
    which is the distinction reading order needs and geometry cannot supply.
    """

    bbox: BoundingBox
    score: float
    label: str = UNTYPED


#: Detectors return LayoutDetections. Bare `(bbox, score)` tuples are still
#: accepted because that was the original signature and the seam is public;
#: they arrive as UNTYPED, which is exactly what they always produced.
Detection = Union[LayoutDetection, Tuple[BoundingBox, float]]
Detector = Callable[[bytes], Sequence[Detection]]


def _normalise(detection: Detection) -> LayoutDetection:
    if isinstance(detection, LayoutDetection):
        return detection
    box, score = detection
    return LayoutDetection(bbox=box, score=score)


class LayoutAnalyzer:
    """Detects layout regions (caption, title, picture, ...) from a rendered page image.

    The detector callable performs the actual ML inference. When no detector is
    supplied, a deterministic heuristic stub is used so the pipeline is fully
    testable without downloading heavy model weights.

    **Still a stub in practice**: no detector ships, so no layout region is
    produced by default. The seam is now capable of carrying a label, which it
    was not before — the docstring promised "heading, figure, paragraph" while
    the signature could only return a box and a score, so every region came out
    as an untyped rectangle no matter what the model had decided.
    """

    ENGINE = "layout-heuristic-v1"

    def __init__(self, detector: Optional[Detector] = None):
        self._detector = detector

    def _engine(self) -> str:
        """Provenance must name whichever engine actually produced the result.

        A detector may declare its own `engine`; otherwise this is the built-in
        heuristic. Reporting the heuristic's name for a model's output would
        make provenance a lie and break the swappability claim (§26.11).
        """
        return getattr(self._detector, "engine", None) or self.ENGINE

    def analyze(self, image_bytes: bytes, page_num: int = 0) -> List[Element]:
        raw = self._detector(image_bytes) if self._detector else self._heuristic(image_bytes)
        elements = []
        for idx, detection in enumerate(raw):
            found = _normalise(detection)
            # An unknown label is reported as UNTYPED rather than passed
            # through: `type` is a bare string, so a typo in a detector would
            # otherwise become a silent new element type that nothing handles.
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
