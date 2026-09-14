"""Tesseract behind the `OCRAnalyzer` detector seam.

Chosen over PaddleOCR/EasyOCR because it is the only mainstream engine that
needs no torch and no GPU: a ~5 MB C++ binary plus one ~2-15 MB language file
each. That keeps §24's resource-lightness intact — the constraint that ruled
out the model-benchmark work in phase 4.

Install (the binary is NOT a pip package):

    Windows:  winget install UB-Mannheim.TesseractOCR
    Debian:   apt install tesseract-ocr tesseract-ocr-ben tesseract-ocr-ara
    macOS:    brew install tesseract tesseract-lang
    then:     pip install -r requirements-ocr.txt

Language data must match the document: `lang="ben"` for Bengali, `"ara"` for
Arabic, `"ben+eng"` for a bilingual page. The default `"eng"` on a Bengali scan
does not fail — it returns confident Latin gibberish, which is worse.
"""

import io
from collections import OrderedDict
from typing import Dict, List, Tuple

from docyx.analysis.ocr import OCRLine
from docyx.core.geometry import BoundingBox


def _require_deps():
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised by the skip in tests
        raise ImportError(
            "TesseractDetector needs the optional OCR stack. "
            "Install it with: pip install -r requirements-ocr.txt"
        ) from exc


class TesseractDetector:
    """An `OCRAnalyzer` detector: page image bytes in, OCRLines out.

    Words are grouped back into lines by Tesseract's own (block, paragraph,
    line) numbering rather than by geometry. The engine already segmented the
    page; re-deriving lines from word boxes would be a second, worse segmenter
    disagreeing with the first — and it is the same line-granularity decision
    the native extractor makes (§5).
    """

    #: Reported as provenance.engine, so output traces to the recogniser.
    engine = "tesseract"

    def __init__(self, lang: str = "eng", config: str = ""):
        _require_deps()
        self.lang = lang
        self.config = config

    def __call__(self, image_bytes: bytes) -> List[OCRLine]:
        import pytesseract
        from PIL import Image

        data = pytesseract.image_to_data(
            Image.open(io.BytesIO(image_bytes)),
            lang=self.lang,
            config=self.config,
            output_type=pytesseract.Output.DICT,
        )

        lines: "OrderedDict[Tuple[int, int, int], Dict]" = OrderedDict()
        for i, word in enumerate(data["text"]):
            confidence = float(data["conf"][i])
            # -1 marks the structural rows (page, block, paragraph, line) that
            # image_to_data interleaves with the words. They carry no text.
            if confidence < 0 or not word.strip():
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            box = (
                float(data["left"][i]),
                float(data["top"][i]),
                float(data["left"][i] + data["width"][i]),
                float(data["top"][i] + data["height"][i]),
            )
            entry = lines.setdefault(key, {"words": [], "scores": [], "box": box})
            entry["words"].append(word)
            entry["scores"].append(confidence / 100.0)
            entry["box"] = _union(entry["box"], box)

        return [
            OCRLine(
                bbox=_bbox(entry["box"]),
                text=" ".join(entry["words"]),
                # Mean, not min: one hard glyph in a long line should not
                # condemn the line, and the caller filters on this value.
                score=sum(entry["scores"]) / len(entry["scores"]),
            )
            for entry in lines.values()
        ]


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _bbox(box) -> BoundingBox:
    x0, y0, x1, y1 = box
    return BoundingBox(x=x0, y=y0, width=max(x1 - x0, 0.0), height=max(y1 - y0, 0.0))
