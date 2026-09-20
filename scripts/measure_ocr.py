"""How much does OCR lose against the native text layer?

Takes a born-digital page, throws its text layer away by rendering it to pixels
and rebuilding an image-only PDF, then reads it back with OCR. The native text
is the reference, so this measures the recogniser rather than the page.

    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_ocr.py .corpus/wiki_ar.pdf 6 ara

A flattened page is clean, deskewed and noise-free, so these numbers are a
CEILING. Treat a bad result here as conclusive and a good one as
necessary-but-not-sufficient.

`--truth FILE` is the other half, and the only mode that touches a page nobody
generated: a real scan, graded against a hand-typed reference, because the page
has no text layer to grade against.

    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_ocr.py \
        --truth .corpus/truth/ocr/real_81_annexure.p0.json \
        .corpus/real/81_Annexure-1.pdf 0 ben+eng

The truth file carries the PDF's sha256 and the run refuses to score a
different file, because a reference typed from one scan says nothing about
another.
"""

import difflib
import hashlib
import json
import pathlib
from collections import Counter
import sys
import unicodedata

import cv2
import fitz
import numpy as np

from docyx.analysis.detectors.tesseract import TesseractDetector
from docyx.analysis.ocr import OCRAnalyzer
from docyx.core.constants import SCALE
from docyx.pipeline.extractor import DocyxPipeline

for channel in (sys.stdout, sys.stderr):
    if hasattr(channel, "reconfigure"):
        channel.reconfigure(encoding="utf-8")


#: Degradations a real scanner introduces and a flattened page does not, as
#: (label, skew degrees, JPEG quality, gaussian noise sigma). The last row is
#: not a worst case — it is an ordinary office scan of a photocopy.
DEGRADATIONS = [
    ("clean (flattened only)", 0.0, None, 0.0),
    ("skew 1.5deg", 1.5, None, 0.0),
    ("jpeg q40", 0.0, 40, 0.0),
    ("noise sigma 12", 0.0, None, 12.0),
    ("ordinary office scan", 1.5, 40, 12.0),
    ("skew 5deg", 5.0, None, 0.0),
    # The last row must FAIL. A sweep whose every row passes cannot tell a
    # robust engine from a harness that is not degrading anything — the same
    # fixture trap that made this repo's synthetic column tests vacuous.
    ("bad photocopy (expect fail)", 7.0, 10, 45.0),
]


def degrade(png: bytes, skew: float, jpeg_quality, noise: float) -> bytes:
    """Approximate a scanner: page skew, JPEG artefacts, sensor noise.

    ponytail: three effects, no show-through, no lighting gradient, no paper
    texture. It bounds the risk rather than removing it — a real scan is still
    the only thing that settles this.
    """
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)

    if skew:
        h, w = img.shape
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), skew, 1.0)
        # White border, not black: a black fill would be read as ink and change
        # the binarisation the recogniser does internally.
        img = cv2.warpAffine(img, matrix, (w, h), borderValue=255)
    if noise:
        img = np.clip(img + np.random.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    if jpeg_quality:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)

    return cv2.imencode(".png", img)[1].tobytes()


def flatten_to_image(pdf_path: str, page_num: int, degradation=None) -> bytes:
    """The same page with its text layer destroyed — what a scan of it is."""
    src = fitz.open(pdf_path)
    pix = src[page_num].get_pixmap(matrix=fitz.Matrix(SCALE, SCALE))
    png = pix.tobytes("png")
    if degradation:
        png = degrade(png, *degradation)

    out = fitz.open()
    page = out.new_page(width=src[page_num].rect.width, height=src[page_num].rect.height)
    page.insert_image(page.rect, stream=png)
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
    diagnose(overlap, ratio, confidences, reference_is_native=True)
    return 0


def diagnose(overlap, ratio, confidences, reference_is_native: bool) -> None:
    """Say whether a low sequence score is misrecognition or misordering.

    Called from BOTH measurement paths. It lived only in the flattened-page
    path, so the `--truth` run -- the one that grades a real scanner, and the
    only evidence in this repo that is not a ceiling -- printed a 0.31 gap
    between the two metrics with nothing saying which one to believe. The
    `oct222013smespdl02_mou.pdf` p13 signature block reads CER 0.340 against
    overlap 0.988: recognition is essentially perfect and the whole "error" is
    two-column reading order.
    """
    if overlap - ratio > 0.3:
        print("\n  The characters agree but their ORDER does not — recognition is "
              "fine.\n  This is a reading-order difference, not a recognition "
              "failure.")
        if reference_is_native:
            print("  On an RTL page it usually means the NATIVE layer is in visual "
                  "order\n  (check the page's RTL_VISUAL_ORDER warning) and OCR "
                  "recovered logical\n  order — i.e. the reference is wrong, not the "
                  "recogniser.")
        else:
            # A hand-typed reference cannot be in visual order, so the
            # remaining sources are a multi-column page and the transcriber's
            # own choice of order -- which the truth file's `caveat` records.
            print("  The reference was typed by hand, so visual order is not the "
                  "cause:\n  read the truth file's `caveat`, and quote character "
                  "overlap here.")
    elif confidences and sum(confidences) / len(confidences) > 0.8 and overlap < 0.8:
        # The dangerous case: confident about characters that are not there.
        print("\n  WARNING: high confidence, low overlap — the engine is confidently wrong")


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate: edits per reference character, the OCR standard.

    `difflib` counts matches, so errors are everything else. Reported next to
    similarity because a ratio of 0.9 sounds good and a CER of 0.10 does not,
    and the second framing is the one the literature uses.
    """
    matches = sum(b.size for b in
                  difflib.SequenceMatcher(None, reference, hypothesis).get_matching_blocks())
    return (max(len(reference), len(hypothesis)) - matches) / max(len(reference), 1)


def against_truth(pdf_path: str, page_num: int, lang: str, truth_path: str) -> int:
    """Score OCR of a REAL scan against a hand-typed reference.

    `main` destroys a born-digital page's text layer and grades against the
    text it threw away, which measures the recogniser on a clean, deskewed,
    noise-free page — a ceiling. This grades the thing that was never measured:
    an actual scanner's output, where the reference had to be typed by a human
    because the page has no text layer at all.
    """
    truth = json.loads(pathlib.Path(truth_path).read_text(encoding="utf-8"))
    digest = hashlib.sha256(pathlib.Path(pdf_path).read_bytes()).hexdigest()
    if digest != truth.get("sha256"):
        print(f"REFUSING: {pdf_path} is not the file this truth was typed from.\n"
              f"  truth  {truth.get('sha256', '')[:16]}\n  actual {digest[:16]}")
        return 2

    detector = TesseractDetector(lang=lang)
    if not set(lang.split("+")) <= set(detector.languages()):
        print(f"language data for {lang!r} is not installed; have {detector.languages()}")
        return 2

    # No flattening: the page IS an image, which is the whole point.
    page = DocyxPipeline(ocr_analyzer=OCRAnalyzer(detector=detector)).process(
        pdf_path, "scan", pages=[page_num]
    ).pages[0]
    recognised = normalise(page_text(page))
    reference = normalise(" ".join(truth["lines"]))

    ratio = difflib.SequenceMatcher(None, reference, recognised).ratio()
    a = Counter(c for c in reference if not c.isspace())
    b = Counter(c for c in recognised if not c.isspace())
    overlap = sum((a & b).values()) / max(sum(a.values()), 1)
    confidences = [el.confidence.value for el in page.elements if el.text]

    bengali = {c for c in reference if "ঀ" <= c <= "৿"}
    found = {c for c in recognised if "ঀ" <= c <= "৿"}

    print(f"{pdf_path} p{page_num}  lang={lang}   REAL SCAN")
    print(f"  status            {page.status.value}  ({[w.code for w in page.warnings]})")
    print(f"  source_type       {page.source_type}")
    print(f"  lines             {len(truth['lines'])} typed -> {len(confidences)} ocr")
    print(f"  characters        {len(reference)} typed -> {len(recognised)} ocr")
    print(f"  CER               {cer(reference, recognised):.3f}   <- errors per reference char")
    print(f"  similarity        {ratio:.3f}   <- sequence; order-sensitive")
    print(f"  character overlap {overlap:.3f}   <- multiset; order-insensitive")
    print(f"  bengali glyphs    {len(bengali & found)}/{len(bengali)} of the distinct ones present")
    if confidences:
        print(f"  mean confidence   {sum(confidences) / len(confidences):.3f}   "
              f"(min {min(confidences):.2f})")
    print(f"\n  note: {truth.get('caveat', '')}")
    print(f"  excludes: {truth.get('excludes', '')}")
    print("\n  typed:", reference[:150])
    print("  ocr  :", recognised[:150])
    diagnose(overlap, ratio, confidences, reference_is_native=False)
    return 0


def sweep(pdf_path: str, page_num: int, lang: str) -> int:
    """How fast does accuracy fall as the page gets more scan-like?

    The single clean number is a ceiling and reads as reassurance. The shape of
    the fall is the useful part: an engine that holds up to skew and collapses
    on JPEG needs a different fix from one that degrades evenly.
    """
    native = page_text(DocyxPipeline().process(pdf_path, "native", pages=[page_num]).pages[0])
    if not native.strip():
        print(f"{pdf_path} p{page_num} has no native text to compare against")
        return 2

    detector = TesseractDetector(lang=lang)
    if not set(lang.split("+")) <= set(detector.languages()):
        print(f"language data for {lang!r} is not installed; have {detector.languages()}")
        return 2
    pipeline = DocyxPipeline(ocr_analyzer=OCRAnalyzer(detector=detector))

    print(f"{pdf_path} p{page_num}  lang={lang}   degradation sweep")
    print(f"  {'condition':<24} {'overlap':>8} {'conf':>7} {'lines':>6}")
    for label, skew, quality, noise in DEGRADATIONS:
        page = pipeline.process(
            flatten_to_image(pdf_path, page_num, (skew, quality, noise)), "ocr"
        ).pages[0]
        text = page_text(page)
        a = Counter(c for c in normalise(native) if not c.isspace())
        b = Counter(c for c in normalise(text) if not c.isspace())
        overlap = sum((a & b).values()) / max(sum(a.values()), 1)
        scores = [el.confidence.value for el in page.elements if el.text]
        mean = sum(scores) / len(scores) if scores else 0.0
        print(f"  {label:<24} {overlap:>8.3f} {mean:>7.3f} {len(scores):>6}")

    print("\n  Overlap is order-insensitive, so a broken native reference (RTL or")
    print("  glyph order) shifts every row equally and the SHAPE stays readable.")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    truth = None
    if "--truth" in argv:
        at = argv.index("--truth")
        truth = argv[at + 1]
        argv = argv[:at] + argv[at + 2:]
    args = [a for a in argv if a != "--sweep"]
    if len(args) != 3:
        print(__doc__)
        raise SystemExit(2)
    if truth:
        raise SystemExit(against_truth(args[0], int(args[1]), args[2], truth))
    run = sweep if "--sweep" in argv else main
    raise SystemExit(run(args[0], int(args[1]), args[2]))
