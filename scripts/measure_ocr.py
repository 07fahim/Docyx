"""How much does OCR lose against the native text layer?

Takes a born-digital page, throws its text layer away by rendering it to pixels
and rebuilding an image-only PDF, then reads it back with OCR. The native text
is the reference, so this measures the recogniser rather than the page.

    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_ocr.py .corpus/wiki_ar.pdf 6 ara

What it does NOT measure: a real scan. A synthetic one is clean, deskewed and
noise-free, so these numbers are a CEILING — real scans have JPEG artefacts,
skew and show-through, and will score below this. Treat a bad result here as
conclusive and a good one as necessary-but-not-sufficient.
"""

import difflib
from collections import Counter
import sys
import unicodedata

import fitz

from docyx.analysis.detectors.tesseract import TesseractDetector
from docyx.analysis.ocr import OCRAnalyzer
from docyx.core.constants import SCALE
from docyx.pipeline.extractor import DocyxPipeline

for channel in (sys.stdout, sys.stderr):
    if hasattr(channel, "reconfigure"):
        channel.reconfigure(encoding="utf-8")


def flatten_to_image(pdf_path: str, page_num: int) -> bytes:
    """The same page with its text layer destroyed — what a scan of it is."""
    src = fitz.open(pdf_path)
    pix = src[page_num].get_pixmap(matrix=fitz.Matrix(SCALE, SCALE))
    out = fitz.open()
    page = out.new_page(width=src[page_num].rect.width, height=src[page_num].rect.height)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    data = out.tobytes()
    out.close()
    src.close()
    return data


def page_text(page) -> str:
    return " ".join(el.text for el in page.elements if el.text)


def normalise(text: str) -> str:
    """Compare meaning, not spacing. NFC because OCR and the PDF may differ in
    composition without differing in content."""
    return unicodedata.normalize("NFC", " ".join(text.split()))


def main(pdf_path: str, page_num: int, lang: str) -> int:
    native_page = DocyxPipeline().process(pdf_path, "native", pages=[page_num]).pages[0]
    native = page_text(native_page)
    if not native.strip():
        print(f"{pdf_path} p{page_num} has no native text to compare against")
        return 2

    detector = TesseractDetector(lang=lang)
    if not set(lang.split("+")) <= set(detector.languages()):
        print(f"language data for {lang!r} is not installed; have {detector.languages()}")
        return 2

    scanned = DocyxPipeline(ocr_analyzer=OCRAnalyzer(detector=detector)).process(
        flatten_to_image(pdf_path, page_num), "ocr"
    ).pages[0]
    recognised = page_text(scanned)

    ratio = difflib.SequenceMatcher(None, normalise(native), normalise(recognised)).ratio()
    # Sequence similarity conflates "recognised the wrong characters" with
    # "recognised the right characters in a different order". On RTL pages
    # those are opposite verdicts, because the NATIVE layer is the one storing
    # visual order — so measure the multiset too and report both.
    a = Counter(c for c in normalise(native) if not c.isspace())
    b = Counter(c for c in normalise(recognised) if not c.isspace())
    overlap = sum((a & b).values()) / max(sum(a.values()), 1)
    confidences = [el.confidence.value for el in scanned.elements if el.text]

    print(f"{pdf_path} p{page_num}  lang={lang}")
    print(f"  status            {scanned.status.value}  ({[w.code for w in scanned.warnings]})")
    print(f"  lines             {len(native_page.elements)} native -> {len(confidences)} ocr")
    print(f"  characters        {len(normalise(native))} native -> {len(normalise(recognised))} ocr")
    print(f"  similarity        {ratio:.3f}   <- sequence; order-sensitive")
    print(f"  character overlap {overlap:.3f}   <- multiset; order-insensitive")
    if confidences:
        print(f"  mean confidence   {sum(confidences) / len(confidences):.3f}   "
              f"(min {min(confidences):.2f})")
    print()
    print("  native :", normalise(native)[:160])
    print("  ocr    :", normalise(recognised)[:160])
    if overlap - ratio > 0.3:
        print(
            "\n  The characters agree but their ORDER does not. On an RTL page that\n"
            "  usually means the native layer is in visual order (check the page's\n"
            "  RTL_VISUAL_ORDER warning) and OCR recovered logical order — i.e. the\n"
            "  reference is wrong, not the recogniser."
        )
    elif confidences and sum(confidences) / len(confidences) > 0.8 and overlap < 0.8:
        # The dangerous case: confident about characters that are not there.
        print("\n  WARNING: high confidence, low overlap — the engine is confidently wrong")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], int(sys.argv[2]), sys.argv[3]))
