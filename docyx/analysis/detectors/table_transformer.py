"""Table Transformer (TATR) behind the `TableAnalyzer` detector seam.

Two models, as the plan's §11 distinction between detection and structure
requires: one finds table regions, a second recovers rows, columns and headers
inside each region. Both are Microsoft's, MIT-licensed, and run on CPU.

Structure recognition returns rows and columns as separate overlapping strips,
not cells — the cell grid is their intersection, which is what this builds. Row
strips tagged as a header contribute the header flag.

Coordinates in and out are the canonical 150 DPI reference pixels (§16); the
page image the detector receives is already rendered at that scale, so no
conversion is needed.

Optional dependency: `pip install -r requirements-models.txt`.
"""

import io
from typing import List, Optional, Tuple

from docyx.analysis.tables import CellDetection, TableDetection
from docyx.core.geometry import BoundingBox

DETECTION_MODEL = "microsoft/table-transformer-detection"
STRUCTURE_MODEL = "microsoft/table-transformer-structure-recognition"

# TATR crops tables tightly; a small margin stops edge rows and columns being
# clipped away before structure recognition sees them.
CROP_PADDING = 12.0


def _require_deps():
    try:
        import torch  # noqa: F401
        from PIL import Image  # noqa: F401
        from transformers import AutoImageProcessor, TableTransformerForObjectDetection  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised by the skip in tests
        raise ImportError(
            "TableTransformerDetector needs the optional model stack. "
            "Install it with: pip install -r requirements-models.txt"
        ) from exc


class TableTransformerDetector:
    """A `TableAnalyzer` detector: page image bytes in, TableDetections out.

    Models load lazily on first call, so constructing the detector is cheap and
    a pipeline can be assembled before deciding to run it.
    """

    #: Reported as provenance.engine, so output is traceable to this model
    #: rather than to the heuristic it replaced.
    engine = "microsoft/table-transformer"

    def __init__(
        self,
        detection_threshold: float = 0.7,
        structure_threshold: float = 0.6,
        device: str = "cpu",
    ):
        _require_deps()
        self.detection_threshold = detection_threshold
        self.structure_threshold = structure_threshold
        self.device = device
        self._detection = None
        self._structure = None

    # -- model loading -----------------------------------------------------

    def _load(self, name: str):
        from transformers import AutoImageProcessor, TableTransformerForObjectDetection

        processor = AutoImageProcessor.from_pretrained(name)
        model = TableTransformerForObjectDetection.from_pretrained(name).to(self.device)
        model.eval()
        return processor, model

    def _detection_model(self):
        if self._detection is None:
            self._detection = self._load(DETECTION_MODEL)
        return self._detection

    def _structure_model(self):
        if self._structure is None:
            self._structure = self._load(STRUCTURE_MODEL)
        return self._structure

    # -- inference ---------------------------------------------------------

    def _run(self, models, image, threshold: float):
        import torch

        processor, model = models
        inputs = processor(images=image, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = model(**inputs)
        target = torch.tensor([[image.height, image.width]])
        results = processor.post_process_object_detection(
            outputs, threshold=threshold, target_sizes=target
        )[0]
        return [
            (model.config.id2label[int(label)], [float(v) for v in box], float(score))
            for label, box, score in zip(results["labels"], results["boxes"], results["scores"])
        ]

    def __call__(self, image_bytes: bytes) -> List[TableDetection]:
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        tables = []
        for label, box, score in self._run(self._detection_model(), image, self.detection_threshold):
            if "table" not in label:
                continue
            tables.append(TableDetection(bbox=_bbox(box), score=score, cells=self._cells(image, box)))
        return tables

    def _cells(self, image, table_box: List[float]) -> List[CellDetection]:
        """Recover the cell grid by intersecting detected row and column strips."""
        x0, y0, x1, y1 = _pad(table_box, image.width, image.height)
        crop = image.crop((x0, y0, x1, y1))
        if crop.width < 8 or crop.height < 8:
            return []

        rows: List[Tuple[float, float, float, bool]] = []
        columns: List[Tuple[float, float, float]] = []
        for label, box, score in self._run(self._structure_model(), crop, self.structure_threshold):
            bx0, by0, bx1, by1 = box
            if label == "table row":
                rows.append((by0, by1, score, False))
            elif label == "table column header":
                rows.append((by0, by1, score, True))
            elif label == "table column":
                columns.append((bx0, bx1, score))

        if not rows or not columns:
            return []

        rows.sort(key=lambda r: r[0])
        columns.sort(key=lambda c: c[0])

        cells = []
        for r_index, (top, bottom, r_score, _) in enumerate(rows):
            for c_index, (left, right, c_score) in enumerate(columns):
                cells.append(
                    CellDetection(
                        # back to page coordinates
                        bbox=BoundingBox(
                            x=x0 + left,
                            y=y0 + top,
                            width=max(right - left, 0.0),
                            height=max(bottom - top, 0.0),
                        ),
                        row=r_index,
                        column=c_index,
                        score=min(r_score, c_score),
                    )
                )
        return cells


def _bbox(box: List[float]) -> BoundingBox:
    x0, y0, x1, y1 = box
    return BoundingBox(x=x0, y=y0, width=max(x1 - x0, 0.0), height=max(y1 - y0, 0.0))


def _pad(box: List[float], width: int, height: int, padding: float = CROP_PADDING):
    x0, y0, x1, y1 = box
    return (
        max(x0 - padding, 0.0),
        max(y0 - padding, 0.0),
        min(x1 + padding, float(width)),
        min(y1 + padding, float(height)),
    )
