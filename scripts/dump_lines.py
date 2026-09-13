"""Dump a page's text lines in neutral (y, x) order for hand-labelling.

Reading order cannot be graded against PyMuPDF or Docling without repeating the
circularity that makes the existing 86% figure meaningless — both are tools
under test. The only non-circular yardstick is a human-ordered page.

This dumps the raw line inventory so a person can write down the true order.
Lines are numbered in naive top-to-bottom, left-to-right order, which is
deliberately NOT Docyx's answer: anchoring the labeller on the output being
graded would bias the ground truth toward it.

    python scripts/dump_lines.py .corpus/arxiv_attention.pdf 2

Emits `lines_checksum`, which scripts/measure_reading_order.py uses to detect a
ground-truth file that has gone stale against changed extraction.
"""

import hashlib
import json
import sys

from docyx.analysis.reading_order import ORDERABLE_TYPES
from docyx.pipeline.extractor import DocyxPipeline


def neutral_lines(page):
    """Text elements in naive (y, x) order — the labelling worksheet."""
    text = [el for el in page.elements if el.type in ORDERABLE_TYPES]
    return sorted(text, key=lambda el: (el.geometry.bbox.y, el.geometry.bbox.x))


def checksum(lines) -> str:
    """Fingerprint of the line inventory, ignoring order.

    Guards the ground truth: if extraction changes what the lines *are*, the
    recorded order no longer refers to the same objects and must be redone.
    """
    parts = sorted(
        f"{round(el.geometry.bbox.x)},{round(el.geometry.bbox.y)},{el.text[:40]}"
        for el in lines
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def main() -> int:
    # Real pages carry typographic quotes and maths; the Windows console
    # default (cp1252) cannot encode them and would kill the dump mid-page.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    pdf_path, page_num = sys.argv[1], int(sys.argv[2])
    doc = DocyxPipeline().process(pdf_path, document_id=pdf_path)
    page = doc.pages[page_num]
    lines = neutral_lines(page)

    print(f"# {pdf_path} page {page_num}")
    print(f"# status={page.status.value} lines={len(lines)} checksum={checksum(lines)}")
    for index, el in enumerate(lines):
        bbox = el.geometry.bbox
        snippet = el.text[:90].replace("\n", " ")
        print(f"{index:3d} | x={round(bbox.x):5d} y={round(bbox.y):5d} "
              f"w={round(bbox.width):4d} | {snippet}")

    print(json.dumps({"checksum": checksum(lines), "line_count": len(lines)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
