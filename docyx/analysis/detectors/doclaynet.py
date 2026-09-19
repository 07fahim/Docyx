"""A DocLayNet layout model behind the LayoutAnalyzer detector seam.

Deformable DETR trained on DocLayNet's 11 classes. Same optional stack as the
table detector, so this adds no dependency, only ~160 MB of weights.

Note: it does not reliably detect Title, and region grouping did not improve
reading order. See CLAUDE.md before building on it.
"""

import io
from typing import List

from docyx.analysis.layout import UNTYPED, LayoutDetection
from docyx.core.geometry import BoundingBox

MODEL = "Aryn/deformable-detr-DocLayNet"

#: Explicit rather than lowercasing on the fly, so a renamed class fails
#: loudly here instead of silently becoming UNTYPED.
LABEL_MAP = {
    "Caption": "caption",
    "Footnote": "footnote",
    "Formula": "formula",
    "List-item": "list_item",
    "Page-footer": "page_footer",
    "Page-header": "page_header",
    "Picture": "picture",
    "Section-header": "section_header",
    "Table": "table",
    # Not "text": that is the extractor's type for a LINE, and sharing the
    # name would put a container into the reading order.
    "Text": "text_region",
    "Title": "title",
}


def _require_deps():
    try:
        import torch  # noqa: F401
        from PIL import Image  # noqa: F401
        from transformers import AutoImageProcessor  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised by the skip in tests
        raise ImportError(
            "DocLayNetDetector needs the optional model stack. "
            "Install it with: pip install -r requirements-models.txt"
        ) from exc


class DocLayNetDetector:
    """A LayoutAnalyzer detector: page image bytes in, LayoutDetections out.

    Weights load lazily, so constructing the detector is cheap.
    """

    #: Reported as provenance.engine.
    engine = MODEL

    def __init__(self, threshold: float = 0.7, device: str = "cpu"):
        _require_deps()
        self.threshold = threshold
        self.device = device
        self._model = None

    def _load(self):
        if self._model is None:
            from transformers import AutoImageProcessor, AutoModelForObjectDetection

            processor = AutoImageProcessor.from_pretrained(MODEL)
            model = AutoModelForObjectDetection.from_pretrained(MODEL).to(self.device)
            model.eval()
            self._model = (processor, model)
        return self._model

    def __call__(self, image_bytes: bytes) -> List[LayoutDetection]:
        import torch
        from PIL import Image

        processor, model = self._load()
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        inputs = processor(images=image, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = model(**inputs)
        results = processor.post_process_object_detection(
            outputs,
            threshold=self.threshold,
            target_sizes=torch.tensor([[image.height, image.width]]),
        )[0]

        detections = []
        for label, box, score in zip(results["labels"], results["boxes"], results["scores"]):
            name = model.config.id2label[int(label)]
            x0, y0, x1, y1 = (float(v) for v in box)
            detections.append(
                LayoutDetection(
                    # Already 150 DPI: the page image was rendered at it.
                    bbox=BoundingBox(
                        x=x0, y=y0, width=max(x1 - x0, 0.0), height=max(y1 - y0, 0.0)
                    ),
                    score=float(score),
                    label=LABEL_MAP.get(name, UNTYPED),
                )
            )
        return detections
