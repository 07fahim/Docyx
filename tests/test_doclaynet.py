"""DocLayNet layout detector.

Split the same way as the table detector: the label mapping is tested with the
model faked out so it runs everywhere, and the real model is exercised only
when the optional stack is installed.
"""

import pytest

from docyx.analysis.detectors.doclaynet import LABEL_MAP
from docyx.analysis.layout import LAYOUT_LABELS, UNTYPED, LayoutAnalyzer, LayoutDetection
from docyx.core.geometry import BoundingBox

transformers_available = True
try:  # pragma: no cover - environment dependent
    import torch  # noqa: F401
    import transformers  # noqa: F401
    from PIL import Image  # noqa: F401
except ImportError:  # pragma: no cover
    transformers_available = False

needs_models = pytest.mark.skipif(
    not transformers_available, reason="optional model stack not installed"
)


def test_every_doclaynet_label_maps_into_the_vocabulary():
    """A model class that this project has no name for would silently become
    UNTYPED on every page, which looks like a model that detects nothing."""
    unmapped = {v for v in LABEL_MAP.values() if v not in LAYOUT_LABELS}
    assert not unmapped, f"detector emits labels the analyzer rejects: {unmapped}"
    assert len(LABEL_MAP) == 11, "DocLayNet has 11 classes"


def test_text_regions_do_not_collide_with_native_text_lines():
    """DocLayNet calls a body region "Text"; the native extractor calls a LINE
    "text". Sharing the name would put a container into ORDERABLE_TYPES and
    number a region alongside the lines it contains."""
    assert LABEL_MAP["Text"] == "text_region"
    assert "text" not in LAYOUT_LABELS


def test_an_unmapped_model_class_becomes_untyped():
    from docyx.analysis.detectors.doclaynet import DocLayNetDetector  # noqa: F401

    def detector(image_bytes):
        return [
            LayoutDetection(
                bbox=BoundingBox(x=0, y=0, width=10, height=10),
                score=0.9,
                label=LABEL_MAP.get("Nonexistent-Class", UNTYPED),
            )
        ]

    assert LayoutAnalyzer(detector=detector).analyze(b"")[0].type == UNTYPED


@needs_models
def test_the_detector_declares_its_engine_without_loading_weights():
    """Constructing must stay cheap: a pipeline is assembled before anyone
    decides to run it, and loading here would download 160 MB to do nothing."""
    from docyx.analysis.detectors.doclaynet import MODEL, DocLayNetDetector

    detector = DocLayNetDetector()

    assert detector.engine == MODEL
    assert detector._model is None, "weights must load lazily, on first call"
    assert LayoutAnalyzer(detector=detector)._engine() == MODEL
