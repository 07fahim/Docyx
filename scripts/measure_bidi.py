"""Measure how a corpus stores right-to-left text, per PDF producer.

    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_bidi.py

Why per *producer*: the RTL findings in this repo were measured on two files
that both came from `Skia/PDF` — Chrome's renderer, via Wikipedia's export
service. A PDF's bidi handling is a property of the tool that wrote it, not of
the script, so one producer proves nothing about Arabic PDFs in general. The
English corpus spans five producers (pdfTeX, Designer, Acrobat Pro, Distiller,
cairo); the non-Latin sample is a monoculture until this table says otherwise.

Columns:

- `rtl_lines`     lines whose characters are majority R/AL (the reliable signal)
- `bidi>0`        spans carrying a non-zero embedding level. Chrome reports 0
                  everywhere, having baked visual order into the glyph stream.
                  A producer that preserves levels would show a count here.
- `visual_order`  pages where mirrored delimiters prove visual-order storage,
                  i.e. pages Docyx degrades to `partial` (RTL_VISUAL_ORDER).

A producer with `rtl_lines > 0` and `visual_order = 0` is the case that has
never been tested: RTL text in correct logical order. Finding one would
validate `_is_rtl` and the RTL column-ordering path against a real document
rather than a unit test.
"""

import collections
import sys
import unicodedata
from pathlib import Path

import fitz

from docyx.pipeline.gate import _rtl_visual_order

CORPUS = Path(".corpus")
MAX_PAGES = 12  # enough to characterise a producer; keeps 800-page files cheap


def scan(path: Path) -> dict:
    doc = fitz.open(path)
    try:
        producer = (doc.metadata or {}).get("producer") or "unknown"
        rtl_lines = bidi_levels = visual_pages = pages = 0

        for page_num in range(min(len(doc), MAX_PAGES)):
            page = doc[page_num]
            pages += 1
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(s.get("text", "") for s in spans)
                    categories = [unicodedata.bidirectional(c) for c in text]
                    rtl = sum(1 for c in categories if c in ("R", "AL"))
                    if rtl > sum(1 for c in categories if c == "L"):
                        rtl_lines += 1
                    bidi_levels += sum(1 for s in spans if int(s.get("bidi", 0)) != 0)
            if _rtl_visual_order(page.get_text("text")):
                visual_pages += 1

        return {
            "file": path.name,
            "producer": producer,
            "pages": pages,
            "rtl_lines": rtl_lines,
            "bidi": bidi_levels,
            "visual": visual_pages,
        }
    finally:
        doc.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rows = [scan(p) for p in sorted(CORPUS.glob("*.pdf"))]
    rtl_rows = [r for r in rows if r["rtl_lines"] > 0]

    header = f"{'file':22s} {'producer':30s} {'pp':>3s} {'rtl_lines':>9s} {'bidi>0':>6s} {'visual':>6s}"
    print(header)
    for r in rows:
        print(
            f"{r['file']:22s} {r['producer'][:30]:30s} {r['pages']:3d} "
            f"{r['rtl_lines']:9d} {r['bidi']:6d} {r['visual']:6d}"
        )

    print()
    if not rtl_rows:
        print("No RTL content in the corpus — the RTL code paths are untested on real files.")
        return 0

    producers = collections.Counter(r["producer"] for r in rtl_rows)
    print(f"RTL files: {len(rtl_rows)} across {len(producers)} producer(s): {dict(producers)}")
    if len(producers) < 2:
        print(
            "MONOCULTURE — every RTL file came from one producer, so none of these\n"
            "findings generalise. Add an Arabic/Hebrew/Urdu PDF from different\n"
            "tooling (Word, InDesign, XeTeX, LibreOffice) and re-run."
        )
    logical = [r for r in rtl_rows if r["visual"] == 0]
    if logical:
        print(f"Logical-order RTL found (never yet tested): {[r['file'] for r in logical]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
