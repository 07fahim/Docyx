"""Wall-clock throughput, so the speed claim has an artifact behind it.

    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_speed.py

**This measures Docyx only, and Docyx at its default configuration is not doing
what a full document-AI stack does.** The default `DocyxPipeline()` runs stub
layout and table detectors that return `[]` (that is the point of the detector
seam) plus an OpenCV heuristic. A tool running a layout model, table-structure
recovery and OCR detection is attempting strictly more work, so a raw ratio
against one measures work not attempted, not efficiency.

Quote this as "native text extraction and rendering, no models", never as a
general speed comparison.
"""

import sys
import time
from pathlib import Path

from docyx.pipeline.extractor import DocyxPipeline

CORPUS = Path(".corpus")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    pdfs = sorted(CORPUS.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs in {CORPUS}")
        return 2

    print(f"{'file':24s} {'pages':>5s} {'total s':>8s} {'s/page':>8s}")
    per_page = []
    for pdf in pdfs:
        started = time.perf_counter()
        document = DocyxPipeline().process(str(pdf), document_id=pdf.name)
        elapsed = time.perf_counter() - started
        pages = len(document.pages)
        rate = elapsed / pages if pages else 0.0
        per_page.append(rate)
        print(f"{pdf.name:24s} {pages:5d} {elapsed:8.2f} {rate:8.3f}")

    ordered = sorted(per_page)
    median = ordered[len(ordered) // 2]
    print()
    print(f"  median {median:.3f} s/page, range {ordered[0]:.3f}-{ordered[-1]:.3f}")
    print("  configuration: default pipeline — stub layout/table detectors, OpenCV visual heuristic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
