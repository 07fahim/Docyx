"""Can a font size be recovered from pixels alone?

`visual_inference` is reserved in the schema for typography estimated from the
image rather than read from the text layer (plan §7, §20). An OCR'd line
carries no typography at all today, which is why a scanned page cannot feed
the heading suggester — and that suggester runs entirely on font size.

This grades the only signal available: an OCR line's bounding box height. Take
a born-digital page, flatten it so the text layer is gone, OCR it, match each
recognised line back to the native line it covers, and compare the height
against the font size the PDF actually declares.

    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_type_size.py .corpus/arxiv_attention.pdf 0 eng

A line's box spans ascender to descender, so it is taller than the em by a
factor that depends on the script and the glyphs that happen to be on the
line. The question is not whether the raw height equals the font size -- it
does not -- but whether the ratio is stable enough that dividing by it
recovers the size, and stable enough to RANK sizes, which is all the heading
suggester needs.
"""

import statistics
import sys

from docyx.analysis.detectors.tesseract import TesseractDetector
from docyx.analysis.ocr import OCRAnalyzer
from docyx.core.constants import SCALE
from docyx.pipeline.extractor import DocyxPipeline

sys.path.insert(0, ".")
from scripts.measure_ocr import flatten_to_image  # noqa: E402

for channel in (sys.stdout, sys.stderr):
    if hasattr(channel, "reconfigure"):
        channel.reconfigure(encoding="utf-8")


def overlap(a, b) -> float:
    """Intersection over the smaller box: an OCR line may split or merge native ones."""
    x = max(0.0, min(a.x + a.width, b.x + b.width) - max(a.x, b.x))
    y = max(0.0, min(a.y + a.height, b.y + b.height) - max(a.y, b.y))
    smaller = min(a.width * a.height, b.width * b.height)
    return (x * y) / smaller if smaller else 0.0


def pairs(native_page, ocr_page):
    """(declared font size in points, recognised box height in points)."""
    native = [
        el for el in native_page.elements
        if el.text and el.text.strip() and el.typography and el.typography.font_size
    ]
    out = []
    for line in ocr_page.elements:
        if not line.text or not line.text.strip():
            continue
        best = max(native, key=lambda el: overlap(line.geometry.bbox, el.geometry.bbox), default=None)
        if best is None or overlap(line.geometry.bbox, best.geometry.bbox) < 0.5:
            continue
        out.append((best.typography.font_size, line.geometry.bbox.height / SCALE))
    return out


def main(pdf_path: str, page_num: int, lang: str) -> int:
    detector = TesseractDetector(lang=lang)
    if not set(lang.split("+")) <= set(detector.languages()):
        print(f"language data for {lang!r} is not installed; have {detector.languages()}")
        return 2

    native_page = DocyxPipeline().process(pdf_path, "native", pages=[page_num]).pages[0]
    ocr_page = DocyxPipeline(ocr_analyzer=OCRAnalyzer(detector=detector)).process(
        flatten_to_image(pdf_path, page_num), "ocr"
    ).pages[0]

    matched = pairs(native_page, ocr_page)
    if len(matched) < 5:
        print(f"{pdf_path} p{page_num}: only {len(matched)} lines matched, not enough to say anything")
        return 2

    ratios = [height / size for size, height in matched]
    median = statistics.median(ratios)
    errors = [abs(height / median - size) / size for size, height in matched]

    print(f"{pdf_path} p{page_num}  {lang}  {len(matched)} lines matched")
    print(f"  height / font_size   median {median:.3f}   sd {statistics.pstdev(ratios):.3f}")
    print(f"  size recovered at that ratio   median error {statistics.median(errors):5.1%}"
          f"   worst {max(errors):5.1%}")
    print(f"  within 10%  {sum(e <= 0.10 for e in errors) / len(errors):5.1%}"
          f"   within 20%  {sum(e <= 0.20 for e in errors) / len(errors):5.1%}")

    # Ranking is the weaker claim and the one the heading suggester rests on.
    body = statistics.median([size for size, _ in matched])
    big_declared = {i for i, (size, _) in enumerate(matched) if size >= body * 1.15}
    big_measured = {i for i, (_, h) in enumerate(matched) if h / median >= body * 1.15}
    if big_declared or big_measured:
        agree = len(big_declared & big_measured)
        print(f"  'bigger than body' agrees on {agree} of {len(big_declared | big_measured)}"
              f"   (declared {len(big_declared)}, measured {len(big_measured)})")
    else:
        print("  no line on this page is bigger than body; ranking untested here")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "eng"))
