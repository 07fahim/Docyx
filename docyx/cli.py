"""Command line entry point.

    python -m docyx document.pdf                  # JSON to stdout
    python -m docyx document.pdf -o out.json      # JSON to a file
    python -m docyx document.pdf -f markdown      # reconstructed prose
    python -m docyx *.pdf -o results/             # batch, one file each
    python -m docyx document.pdf --pages 0-4,9    # selected pages only
    python -m docyx scan.pdf --ocr ben            # recognise a scanned page

Exit codes matter more than the output format for anything scripted:

    0  every page produced a valid result
    1  at least one page was degraded (`partial`) or failed
    2  the input could not be read at all

A page that fails the text-layer gate is NOT an error — it is a documented
outcome carrying its reason, and it still appears in the output. `--strict`
turns any degraded page into exit 1 for callers that want a hard gate.
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from docyx.export.markdown import to_markdown
from docyx.pipeline.extractor import DocyxPipeline
from docyx.schema.models import Document, PageStatus


def parse_pages(spec: Optional[str]) -> Optional[List[int]]:
    """Turn "0-4,9" into [0, 1, 2, 3, 4, 9]. Zero-based, matching the API."""
    if not spec:
        return None
    pages: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            pages.extend(range(int(start), int(end) + 1))
        else:
            pages.append(int(part))
    return pages


def render(document: Document, fmt: str) -> str:
    if fmt == "markdown":
        return to_markdown(document)
    return document.model_dump_json(indent=2, exclude_none=True)


def summarise(document: Document) -> str:
    counts = {status: 0 for status in ("ok", "partial", "failed")}
    for page in document.pages:
        counts[page.status.value] += 1
    issues = sorted(
        {issue.code for page in document.pages for issue in page.warnings + page.errors}
    )
    line = f"{document.document_id}: {len(document.pages)} pages — " + ", ".join(
        f"{n} {name}" for name, n in counts.items() if n
    )
    return line + (f"  [{', '.join(issues)}]" if issues else "")


def main(argv: Optional[List[str]] = None) -> int:
    # Windows consoles default to cp1252, which cannot encode most of what this
    # tool extracts — Arabic, Bengali, CJK, and even the angle brackets in an
    # arXiv paper all raise UnicodeEncodeError and kill the run. Encoding to
    # UTF-8 always succeeds; a console that cannot draw a glyph shows a box,
    # but the bytes written or piped are correct.
    for channel in (sys.stdout, sys.stderr):
        if hasattr(channel, "reconfigure"):
            channel.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="docyx",
        description="Extract structured metadata from born-digital PDFs.",
        epilog="Exit 0 all pages ok, 1 some degraded or failed, 2 unreadable input.",
    )
    parser.add_argument("pdfs", nargs="+", type=Path, help="one or more PDF files")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="file to write, or a directory when several PDFs are given",
    )
    parser.add_argument(
        "-f", "--format", choices=("json", "markdown"), default="json", help="default: json"
    )
    parser.add_argument("--pages", help='zero-based, e.g. "0-4,9"')
    parser.add_argument(
        "--ocr",
        metavar="LANG",
        help=(
            "recognise text on pages with no text layer, e.g. ben, ara, ben+eng. "
            "Needs tesseract installed; such pages are `partial`, never `ok`"
        ),
    )
    parser.add_argument(
        "--ocr-min-confidence",
        type=float,
        default=0.4,
        metavar="N",
        help="drop recognised lines below this score (0-1, default 0.4)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if any page is degraded, not only if one fails",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="suppress the per-file summary on stderr"
    )
    args = parser.parse_args(argv)

    try:
        pages = parse_pages(args.pages)
    except ValueError:
        parser.error(f"could not parse --pages {args.pages!r}; expected e.g. 0-4,9")

    # A directory is required for many inputs: writing them all to one file
    # would silently keep only the last.
    many = len(args.pdfs) > 1
    if many and args.output and not args.output.is_dir():
        if args.output.exists():
            parser.error(f"{args.output} is a file; give a directory for several PDFs")
        args.output.mkdir(parents=True, exist_ok=True)

    ocr = None
    if args.ocr:
        # Imported here, not at module scope: the OCR stack is optional, and a
        # missing pytesseract must not stop the CLI running without --ocr.
        from docyx.analysis.detectors.tesseract import TesseractDetector
        from docyx.analysis.ocr import OCRAnalyzer

        try:
            ocr = OCRAnalyzer(
                detector=TesseractDetector(lang=args.ocr),
                min_confidence=args.ocr_min_confidence,
            )
        except ImportError as exc:
            parser.error(str(exc))

    pipeline = DocyxPipeline(ocr_analyzer=ocr)
    worst = 0

    for pdf in args.pdfs:
        if not pdf.is_file():
            print(f"docyx: {pdf}: not a file", file=sys.stderr)
            worst = max(worst, 2)
            continue
        try:
            document = pipeline.process(str(pdf), document_id=pdf.stem, pages=pages)
        except Exception as exc:  # noqa: BLE001 — one bad file must not stop a batch
            print(f"docyx: {pdf}: {type(exc).__name__}: {exc}", file=sys.stderr)
            worst = max(worst, 2)
            continue

        text = render(document, args.format)
        if args.output is None:
            print(text)
        else:
            suffix = ".md" if args.format == "markdown" else ".json"
            target = args.output / f"{pdf.stem}{suffix}" if many else args.output
            target.write_text(text, encoding="utf-8")

        if not args.quiet:
            print(summarise(document), file=sys.stderr)

        statuses = {page.status for page in document.pages}
        if PageStatus.FAILED in statuses or (args.strict and PageStatus.PARTIAL in statuses):
            worst = max(worst, 1)

    return worst


if __name__ == "__main__":
    raise SystemExit(main())
