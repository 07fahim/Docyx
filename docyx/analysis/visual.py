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


#: Share of a candidate figure's area that must be covered by text lines before
#: it is judged to be text. Half, because a chart with axis labels or a photo
#: with a caption inside its box must survive, and neither gets close.
TEXT_COVER = 0.5

#: Below this, a figure is a picture and a straight edge inside it is image
#: content. Above it the box holds text, and a straight edge inside *that* is a
#: table border — a real rule that must survive. Measured text coverage:
#: real pictures 0.04 / 0.06 / 0.08, a ruled NASA table caught as a figure 0.43,
#: a photo group including its caption 0.34, actual text blocks 0.65-0.88.
PICTURE_TEXT = 0.15


def reject_text_figures(
    detections: List[Element],
    text: List[Element],
    images: Optional[List[tuple]] = None,
) -> List[Element]:
    """Drop `figure` boxes that are really text, and rules drawn inside a figure.

    **`max_component_density` is a Latin-only discriminator, and it put Bengali
    and Arabic prose exactly where figures live.** Its premise -- text shatters
    into one connected component per glyph -- is a property of an alphabet with
    separated letters, not of writing. Measured, components per 10k pixels over
    real text lines:

        arxiv_attention p2  latin     median 62.3
        rfc2616 p12         latin     median 43.1
        wiki_bn p30         bengali   median 10.5
        wiki_ar p6          arabic    median  0.0

    against a threshold of 20 and a code comment claiming "figures sit near
    10". Bengali joins its letters under the matra and Arabic is cursive, so a
    word is one component rather than six -- and whole paragraphs were being
    reported as figures on exactly the scripts this project exists for.

    So the pixels get overruled by something script-independent and already
    known: **a region tiled by extracted text lines is text.** This runs in the
    pipeline rather than inside the analyzer because the analyzer takes only
    image bytes -- it must work on a gate-failed page, and on that page there
    are no lines and nothing here fires.

    `text` may be native lines or OCR lines; both answer the same question.
    """
    boxes = [el.geometry.bbox for el in text if el.text]
    kept: List[Element] = []
    figures: List[BoundingBox] = []
    for el in detections:
        if el.type == "figure":
            cover = _covered(el.geometry.bbox, boxes)
            if cover >= TEXT_COVER:
                continue
            # Only a picture suppresses rules. `nasa_budget` p88's ruled table
            # is caught as a figure at 0.43 coverage, and letting it suppress
            # would delete the table's own bottom border — trading a false rule
            # for a missing one, which is the worse defect.
            if cover < PICTURE_TEXT:
                figures.append(el.geometry.bbox)
        kept.append(el)

    # A strong straight edge inside a photograph is image content, not page
    # furniture: the lit facade of a building at night, a horizon, the frame of
    # the image itself. `wiki_bn.pdf` p30 reported five of them.
    #
    # Detected figures AND the rasters the file itself declares. The heuristic
    # alone left one behind on that page — the horizon in a photo whose sky is
    # white, so there was too little ink to contour. The PDF knows where its
    # pictures are; preferring a heuristic over the file's own statement would
    # be the same mistake as reading typography off pixels.
    regions = figures + [BoundingBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0)
                         for x0, y0, x1, y1 in (images or [])]
    return [el for el in kept
            if el.type != "rule" or _covered(el.geometry.bbox, regions) < TEXT_COVER]


def _covered(box: BoundingBox, others: List[BoundingBox]) -> float:
    """Share of `box`'s area overlapped by `others`, counting each pixel once.

    Summing per-box intersections would double-count where two lines overlap
    and could report more than the whole. A coarse occupancy grid is bounded by
    construction and costs nothing at this size.
    """
    if box.width <= 0 or box.height <= 0 or not others:
        return 0.0
    cells = 24
    hit = 0
    for row in range(cells):
        for col in range(cells):
            px = box.x + (col + 0.5) * box.width / cells
            py = box.y + (row + 0.5) * box.height / cells
            if any(o.x <= px <= o.x1 and o.y <= py <= o.y1 for o in others):
                hit += 1
    return hit / (cells * cells)


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
