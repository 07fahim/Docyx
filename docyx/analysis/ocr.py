"""OCR behind the fourth analyzer seam.

The only producer of `provenance.source = ocr` and `confidence.type =
inferred`. Nothing else may emit them: that split is what lets a consumer tell
read text from recognised text without parsing engine names.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional

from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Direction, Element, script_of


@dataclass
class OCRLine:
    """One recognised line. Coordinates are 150-DPI reference pixels, the same
    space the page image was rendered in, so no conversion is needed."""

    bbox: BoundingBox
    text: str
    score: float


Detector = Callable[[bytes], List[OCRLine]]


class OCRAnalyzer:
    """Recognises text from the rendered page image.

    Runs only on gate-failed pages, or on repairable ones when `ocr_repair` is
    set: re-reading a page that has an exact text layer is a downgrade.
    """

    ENGINE = "ocr-stub"

    def __init__(self, detector: Optional[Detector] = None, min_confidence: float = 0.0):
        self._detector = detector
        # Page speckle recognised as a one-character word lands mid-column
        # and derails the reading order around it.
        self.min_confidence = min_confidence

    def _engine(self) -> str:
        return getattr(self._detector, "engine", None) or self.ENGINE

    def analyze(self, image_bytes: bytes, page_num: int = 0) -> List[Element]:
        if self._detector is None:
            return []
        elements = []
        for idx, line in enumerate(self._detector(image_bytes)):
            if not line.text.strip() or line.score < self.min_confidence:
                continue
            elements.append(
                Element(
                    id=f"page{page_num + 1}_ocr_{idx}",
                    type="text",
                    geometry=Geometry(bbox=line.bbox),
                    confidence=Confidence(value=line.score, type=ConfidenceType.INFERRED),
                    provenance=Provenance(
                        source=ProvenanceSource.OCR,
                        engine=self._engine(),
                        raw_confidence=line.score,
                    ),
                    text=line.text,
                    # No typography: a recogniser reports characters only.
                    direction=Direction.of_text(line.text),
                    script=script_of(line.text),
                )
            )
        return elements
