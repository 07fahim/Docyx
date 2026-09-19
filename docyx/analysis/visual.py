from dataclasses import dataclass
from typing import Callable, List, Optional

import cv2
import numpy as np

from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Element


@dataclass
class VisualDetection:
    bbox: BoundingBox
    kind: str  # "rule" | "figure"
    score: float


Detector = Callable[[bytes], List[VisualDetection]]


class VisualAnalyzer:
    """Finds rules and figure blocks in the rendered page image.

    Pixels only, so it runs on gate-failed pages too. Contour heuristics, not
    a model: rules are reliable, figure detection is approximate.
    """

    ENGINE = "visual-opencv-v1"

    def __init__(
        self,
        detector: Optional[Detector] = None,
        min_rule_ratio: float = 0.25,
        max_rule_thickness: float = 12.0,
        min_figure_area_ratio: float = 0.02,
        max_component_density: float = 20.0,
        confidence: float = 0.5,
    ):
        self._detector = detector
        self.min_rule_ratio = min_rule_ratio
        # A rule is long AND thin: without the thinness bound a filled block
        # survives the opening and masquerades as one.
        self.max_rule_thickness = max_rule_thickness
        self.min_figure_area_ratio = min_figure_area_ratio
        # Components per 10k px. Text shatters into one per glyph (27-39 on
        # real pages); figures sit near 10.
        self.max_component_density = max_component_density
        self.confidence = confidence

    def _engine(self) -> str:
        """The detector's own engine name, or the built-in heuristic's."""
        return getattr(self._detector, "engine", None) or self.ENGINE

    def analyze(self, image_bytes: bytes, page_num: int = 0) -> List[Element]:
        detections = (
            self._detector(image_bytes) if self._detector else self._heuristic(image_bytes)
        )
        elements = []
        for idx, det in enumerate(detections):
            elements.append(
                Element(
                    id=f"page{page_num + 1}_visual_{idx}",
                    type=det.kind,
                    geometry=Geometry(bbox=det.bbox),
                    confidence=Confidence(value=det.score, type=ConfidenceType.DETECTED),
                    provenance=Provenance(
                        source=ProvenanceSource.GEOMETRY_INFERENCE,
                        engine=self._engine(),
                        raw_confidence=det.score,
                    ),
                )
            )
        return elements

    def _heuristic(self, image_bytes: bytes) -> List[VisualDetection]:
        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return []
        h, w = img.shape
        # Ink is dark; invert so features are white for morphology.
        ink = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

        detections = self._rules(ink, w, h)
        rule_boxes = [d.bbox for d in detections]
        detections.extend(self._figures(ink, w, h, rule_boxes))
        return detections

    def _rules(self, ink: np.ndarray, w: int, h: int) -> List[VisualDetection]:
        """Long horizontal/vertical strokes: table borders, separators, underlines."""
        out = []
        for axis, kernel_size, min_len in (
            ("h", (max(int(w * self.min_rule_ratio), 1), 1), w * self.min_rule_ratio),
            ("v", (1, max(int(h * self.min_rule_ratio), 1)), h * self.min_rule_ratio),
        ):
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, kernel_size)
            found = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel, iterations=1)
            contours, _ = cv2.findContours(found, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                x, y, cw, ch = cv2.boundingRect(c)
                length, thickness = (cw, ch) if axis == "h" else (ch, cw)
                if length >= min_len and thickness <= self.max_rule_thickness:
                    out.append(
                        VisualDetection(
                            bbox=BoundingBox(x=float(x), y=float(y), width=float(cw), height=float(ch)),
                            kind="rule",
                            score=self.confidence,
                        )
                    )
        return out

    def _figures(
        self, ink: np.ndarray, w: int, h: int, rule_boxes: List[BoundingBox]
    ) -> List[VisualDetection]:
        """Large solid blocks left over once strokes are merged — images, charts."""
        min_area = w * h * self.min_figure_area_ratio
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        blocks = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(blocks, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        out = []
        for c in contours:
            x, y, cw, ch = cv2.boundingRect(c)
            if cw * ch < min_area:
                continue
            box = BoundingBox(x=float(x), y=float(y), width=float(cw), height=float(ch))
            # A block that is really just a thick rule is already reported as one.
            if any(_iou(box, rb) > 0.5 for rb in rule_boxes):
                continue
            # Closing merges a paragraph into a figure-shaped blob; ask the
            # unclosed ink how fragmented it is.
            if _component_density(ink[y : y + ch, x : x + cw]) > self.max_component_density:
                continue
            out.append(VisualDetection(bbox=box, kind="figure", score=self.confidence))
        return out


def _component_density(region: np.ndarray) -> float:
    """Connected components per 10k pixels."""
    if region.size == 0:
        return 0.0
    count = cv2.connectedComponents(region)[0]
    return count / (region.size / 10000.0)


def _iou(a: BoundingBox, b: BoundingBox) -> float:
    ix = max(0.0, min(a.x1, b.x1) - max(a.x, b.x))
    iy = max(0.0, min(a.y1, b.y1) - max(a.y, b.y))
    inter = ix * iy
    union = a.width * a.height + b.width * b.height - inter
    return inter / union if union else 0.0
