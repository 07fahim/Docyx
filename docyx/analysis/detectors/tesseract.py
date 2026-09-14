"""Tesseract behind the `OCRAnalyzer` detector seam.

Chosen over PaddleOCR/EasyOCR on *runtime* footprint, not download size: it is
the only mainstream engine that loads no Python ML framework into the process.
EasyOCR sits around 1.8 GB RSS after init and PaddleOCR around 950 MB, with
8-20 s of cold start; Tesseract is tens of MB and starts immediately. That
keeps §24's resource-lightness intact — the constraint that ruled out the
model-benchmark work in phase 4.

Two corrections to the reasoning as first recorded, since it was wrong: PaddleOCR
runs on PaddlePaddle, not torch, and the "~2 GB" figure was CUDA-bundled torch
when both alternatives install fine from a CPU index. Download size was never the
real difference.

**The open question is accuracy on Bengali, where Tesseract is weakest and
EasyOCR has explicit `bn` support.** If that is what decides it, footprint is
the wrong axis and this adapter is the wrong choice — which is why the engine
sits behind `OCRAnalyzer`'s detector seam and costs one file to replace.

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
import os
import shutil
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from docyx.analysis.ocr import OCRLine
from docyx.core.geometry import BoundingBox

# The Windows installer does not add itself to PATH, so `pip install` succeeds,
# the binary is present, and pytesseract still raises TesseractNotFoundError.
# Measured on the author's own machine — this is the default experience, not an
# edge case, so probe the standard locations rather than making every user
# discover the same thing.
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
    """An `OCRAnalyzer` detector: page image bytes in, OCRLines out.

    Words are grouped back into lines by Tesseract's own (block, paragraph,
    line) numbering rather than by geometry. The engine already segmented the
    page; re-deriving lines from word boxes would be a second, worse segmenter
    disagreeing with the first — and it is the same line-granularity decision
    the native extractor makes (§5).
    """

    #: Reported as provenance.engine, so output traces to the recogniser.
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
