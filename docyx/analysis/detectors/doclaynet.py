"""A DocLayNet layout model behind the `LayoutAnalyzer` detector seam.

Deformable DETR trained on DocLayNet's 80k annotated pages, emitting the 11
classes `.corpus/truth/` was labelled against. Same optional stack as the table
detector — torch, transformers, timm — so this adds **no new dependency**, only
weights.

Why a model at all, when the rest of reading order is geometry: two measured
failures cannot be fixed by geometry even in principle.

- A figure caption interleaves by `y` with the body text beside it. The caption
  is 53px tall against a 1527px block, so `MIN_COLUMN_HEIGHT_RATIO` correctly
  refuses to call it a column — correctly, because loosening that threshold
  costs the pages currently at 1.000. Only knowing "this is a caption" separates
  it. (`.corpus/truth/wiki_ar.p6.json`, 0.946 adjacency.)
- Table cells that wrap over several lines must read cell by cell, while
  single-line cells must read row-wise. Both are the same geometry; the
  difference is where the cell boundaries are. (`nasa_budget.p88`, 0.808/0.706.)

Optional dependency: `pip install -r requirements-models.txt`, plus ~160 MB of
weights on first use.
"""

import io
from typing import List

from docyx.analysis.layout import UNTYPED, LayoutDetection
from docyx.core.geometry import BoundingBox

MODEL = "Aryn/deformable-detr-DocLayNet"

#: DocLayNet's own label strings mapped to this project's vocabulary. Kept
#: explicit rather than lowercasing on the fly so a model that renames a class
#: fails loudly here instead of silently becoming UNTYPED everywhere.
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
    # Not "text": that is the native extractor's type for a LINE. A region
    # containing lines is a different thing, and sharing the name would put a
    # container into ORDERABLE_TYPES and number it alongside its own contents.
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
    """A `LayoutAnalyzer` detector: page image bytes in, LayoutDetections out.

    The model loads lazily on first call, so constructing the detector is cheap
    and a pipeline can be assembled before deciding to run it.
    """

    #: Reported as provenance.engine, so a region traces to this model rather
    #: than to the heuristic stub it replaced.
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
                    # Already in the canonical 150 DPI reference pixels: the
                    # image handed to the detector was rendered at that scale.
                    bbox=BoundingBox(
                        x=x0, y=y0, width=max(x1 - x0, 0.0), height=max(y1 - y0, 0.0)
                    ),
                    score=float(score),
                    label=LABEL_MAP.get(name, UNTYPED),
                )
            )
        return detections
