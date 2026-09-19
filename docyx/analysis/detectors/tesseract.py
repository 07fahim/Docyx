"""Tesseract behind the OCRAnalyzer detector seam.

Chosen on runtime footprint: it loads no Python ML framework into the process
(~1.8 GB RSS for EasyOCR, ~950 MB for PaddleOCR, against tens of MB here).

The engine is not a pip package:

    Windows:  winget install UB-Mannheim.TesseractOCR
    Debian:   apt install tesseract-ocr tesseract-ocr-ben tesseract-ocr-ara
    macOS:    brew install tesseract tesseract-lang

`lang` must match the document: `--ocr eng` on a Bengali scan returns
confident Latin gibberish rather than failing.
"""

import io
import os
import shutil
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from docyx.analysis.ocr import OCRLine
from docyx.core.geometry import BoundingBox

# The Windows installer does not add itself to PATH.
_WINDOWS_DEFAULTS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _find_binary() -> Optional[str]:
    """PATH, then $TESSERACT_CMD, then the usual Windows install locations."""
    return (
        shutil.which("tesseract")
        or os.environ.get("TESSERACT_CMD")
        or next((p for p in _WINDOWS_DEFAULTS if os.path.isfile(p)), None)
    )


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
    """An OCRAnalyzer detector: page image bytes in, OCRLines out.

    Words are regrouped into lines by Tesseract's own (block, paragraph, line)
    numbering rather than by geometry.
    """

    #: Reported as provenance.engine.
    engine = "tesseract"

    def __init__(self, lang: str = "eng", config: str = "", binary: Optional[str] = None):
        _require_deps()
        import pytesseract

        found = binary or _find_binary()
        if not found:
            raise ImportError(
                "tesseract binary not found on PATH, in $TESSERACT_CMD, or at the "
                "default install location. Install it (winget install "
                "UB-Mannheim.TesseractOCR) or pass binary=<path>."
            )
        pytesseract.pytesseract.tesseract_cmd = found

        self.lang = lang
        self.config = config
        self.binary = found

    def languages(self) -> List[str]:
        """Language files actually installed. `lang` must be a subset of these."""
        import pytesseract

        return list(pytesseract.get_languages(config=""))

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
            # -1 marks the structural rows image_to_data interleaves.
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
                # Mean, not min: one hard glyph must not condemn the line.
                score=sum(entry["scores"]) / len(entry["scores"]),
            )
            for entry in lines.values()
        ]


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _bbox(box) -> BoundingBox:
    x0, y0, x1, y1 = box
    return BoundingBox(x=x0, y=y0, width=max(x1 - x0, 0.0), height=max(y1 - y0, 0.0))
