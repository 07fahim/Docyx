"""Inventory every genuinely scanned page in the corpora, by producer.

The OCR evidence in this repo is one real scanned page -- one producer, one
transcriber -- and everything else is a born-digital page flattened to pixels,
which is a ceiling rather than a measurement. Growing that means finding real
scans, and the first question is how many are already on disk.

    PYTHONPATH=. python scripts/find_scanned_pages.py

Cheap on purpose: the gate reads the text layer and `has_images` inspects the
resource dictionary, so nothing is rasterised. A 1300-page sweep is seconds
rather than the five minutes a full pipeline run costs.

A page is reported as `scanned` on exactly the pipeline's own derivation --
gate failed, raster content present -- so this cannot drift from what the
extractor would say. `empty` pages are counted and not listed: a blank page is
not OCR evidence.

Pages carrying `RTL_VISUAL_ORDER` or `COMBINING_MARK_ORDER` are listed too.
They are not scans, but they are the other input `--ocr-repair` exists for, and
the same transcription effort grades both.
"""

import sys
from collections import Counter
from pathlib import Path

from docyx.pdf.renderer import PDFRenderer
from docyx.pipeline.gate import TextLayerGate

#: Every directory holding input PDFs. `.corpus/docling` is another tool's
#: output, not input, so it is deliberately absent.
CORPORA = [Path(".corpus"), Path(".corpus/real"), Path(".corpus/ar")]

#: Text-layer faults that OCR can repair. Same list as REPAIRABLE_CODES, kept
#: as a display grouping rather than imported, because this reports what was
#: found and not what the pipeline would do about it.
REPAIRABLE = ("RTL_VISUAL_ORDER", "COMBINING_MARK_ORDER")


def survey(pdf: Path):
    """(scanned page numbers, empty count, {code: [page numbers]}) for one PDF."""
    gate = TextLayerGate()
    scanned, empty, flagged = [], 0, {}
    with PDFRenderer(str(pdf)) as renderer:
        doc = renderer.text_document()
        producer = (doc.metadata or {}).get("producer") or "unknown"
        for page_num in range(renderer.page_count()):
            try:
                result = gate.check_page(doc, page_num)
            except Exception:
                # A page the gate cannot read is not evidence either way.
                continue
            if not result.passed:
                if renderer.has_images(page_num):
                    scanned.append(page_num)
                else:
                    empty += 1
            elif result.warning is not None:
                flagged.setdefault(result.warning.code, []).append(page_num)
    return scanned, empty, flagged, producer


def _runs(pages):
    """Collapse [0,1,2,7] to "0-2, 7" -- a 200-page scan is one range."""
    if not pages:
        return ""
    out, start, previous = [], pages[0], pages[0]
    for page in pages[1:]:
        if page != previous + 1:
            out.append(f"{start}-{previous}" if start != previous else f"{start}")
            start = page
        previous = page
    out.append(f"{start}-{previous}" if start != previous else f"{start}")
    return ", ".join(out)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pdfs = sorted({p for corpus in CORPORA for p in corpus.glob("*.pdf")})
    if not pdfs:
        print("no PDFs found; run from the repository root")
        return 2

    total_pages = total_scanned = 0
    by_producer: Counter = Counter()
    repairable: Counter = Counter()

    for pdf in pdfs:
        try:
            scanned, empty, flagged, producer = survey(pdf)
        except Exception as exc:
            print(f"  {pdf.name:42s} unreadable: {exc}")
            continue
        with PDFRenderer(str(pdf)) as r:
            pages = r.page_count()
        total_pages += pages
        total_scanned += len(scanned)

        notes = []
        if scanned:
            by_producer[producer] += len(scanned)
            notes.append(f"SCANNED {len(scanned)}/{pages} p[{_runs(scanned)}]")
        if empty:
            notes.append(f"empty {empty}")
        for code, found in flagged.items():
            if code in REPAIRABLE:
                repairable[code] += len(found)
            notes.append(f"{code} {len(found)} p[{_runs(found)}]")
        if notes:
            print(f"  {pdf.name:42s} {'  '.join(notes)}")

    print(f"\n{len(pdfs)} documents, {total_pages} pages")
    print(f"genuinely scanned: {total_scanned} pages "
          f"({total_scanned / total_pages:.1%})")
    if by_producer:
        print("\nscanned pages by producer -- one producer is one scanner "
              "pipeline, so this is the diversity that matters:")
        for producer, count in by_producer.most_common():
            print(f"  {count:5d}  {producer}")
    if repairable:
        print("\ntext layers OCR could repair (not scans, same grading cost):")
        for code, count in repairable.most_common():
            print(f"  {count:5d}  {code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
