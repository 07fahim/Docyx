"""Text recovered from pixels, for pages with no machine-readable text layer.

The fourth analyzer, and deliberately the same shape as the other three: an
optional `detector` does the work, the analyzer only turns detections into
Elements and attaches honest provenance. With no detector injected it returns
nothing, so the default pipeline behaves exactly as it did before OCR existed
and core keeps its four dependencies.

This is where the two reserved schema values finally activate (§16.1/§16.2):
``provenance.source = ocr`` and ``confidence.type = inferred``. Nothing else in
the codebase may emit them — native text stays ``exact``, detector geometry
stays ``detected``. That three-way split is the whole point: a consumer can
tell read text from recognised text without parsing engine names.
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

    Runs only on gate-failed pages (see `DocyxPipeline._process_page`). A page
    that already has a text layer must never be re-read from pixels: the native
    layer is exact by construction and OCR is not, so preferring OCR anywhere
    would be a straight downgrade.
    """

    ENGINE = "ocr-stub"

    def __init__(self, detector: Optional[Detector] = None, min_confidence: float = 0.0):
        self._detector = detector
        # Lines below this are dropped rather than returned with a low score.
        # Tesseract emits confident nonsense from page noise — speckles become
        # single-character "words" — and one such line in the middle of a
        # column derails reading order for the lines around it.
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
                    # No typography: a recogniser reports characters, not the
                    # font that drew them. Leaving it None keeps the markdown
                    # heading heuristic from inventing structure from nothing.
                    direction=Direction.of_text(line.text),
                    script=script_of(line.text),
                )
            )
        return elements
