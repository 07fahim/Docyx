"""Command line entry point.

    python -m docyx document.pdf                  # JSON to stdout
    python -m docyx document.pdf -o out.json      # JSON to a file
    python -m docyx document.pdf -f markdown      # reconstructed prose
    python -m docyx *.pdf -o results/             # batch, one file each
    python -m docyx document.pdf --pages 0-4,9    # selected pages only
    python -m docyx scan.pdf --ocr ben            # recognise a scanned page
    python -m docyx paper.pdf -f bundle -o out/   # directory tree, one file per page
    python -m docyx book.pdf -f blocks -o out/ --layout   # image + annotation pairs

Exit codes matter more than the output format for anything scripted:

    0  every page produced a valid result, or only degraded ones
    1  at least one page FAILED (or, with --strict, was degraded)
    2  the input could not be read at all

A page that fails the text-layer gate is NOT an error — it is a documented
outcome carrying its reason, and it still appears in the output. `--strict`
turns any degraded page into exit 1 for callers that want a hard gate.
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from docyx.export.blocks import write_blocks
from docyx.export.bundle import write_bundle
from docyx.export.markdown import to_markdown

#: Formats that produce a directory rather than a single file.
TREE_FORMATS = frozenset({"bundle", "blocks"})
from docyx.pipeline.extractor import DocyxPipeline
from docyx.schema.models import Document, PageStatus


def ocr_language(value: str) -> str:
    """Reject a filename where a language code belongs.

    `docyx --ocr scan.pdf` otherwise swallows the PDF as the language and then
    reports the PDF as missing, which blames the wrong argument entirely.
    """
    if value.lower().endswith(".pdf") or "/" in value or "\\" in value:
        raise argparse.ArgumentTypeError(
            f"{value!r} looks like a file, not a language code. Write "
            "`--ocr LANG` (ben, ara, ben+eng) and give the PDF separately."
        )
    return value


def ocr_confidence(value: str) -> float:
    """A confidence is a probability, so reject anything outside 0-1.

    `--ocr-min-confidence 50` reads as a percentage and is accepted by float(),
    then drops every recognised line -- so a scan comes back `failed` with
    `NO_TEXT_LAYER` and the page looks unreadable rather than over-filtered.
    Same shape as `--ocr` naming a language tesseract does not have.
    """
    try:
        score = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number")
    if not 0.0 <= score <= 1.0:
        raise argparse.ArgumentTypeError(
            f"{score:g} is not between 0 and 1. This is a probability, not a "
            "percentage: 0.4 keeps lines scoring 40% or better."
        )
    return score


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
    # Windows consoles default to cp1252, which cannot encode most of what
    # this tool extracts and raises UnicodeEncodeError mid-run.
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
        "-f",
        "--format",
        choices=("json", "markdown", "bundle", "blocks"),
        default="json",
        help="bundle and blocks write directory trees; default: json",
    )
    parser.add_argument("--pages", help='zero-based, e.g. "0-4,9"')
    parser.add_argument(
        "--ocr",
        metavar="LANG",
        type=ocr_language,
        help=(
            "recognise text on pages with no text layer, e.g. ben, ara, ben+eng. "
            "Needs tesseract and that language's data installed; such pages are "
            "`partial`, never `ok`"
        ),
    )
    parser.add_argument(
        "--ocr-repair",
        action="store_true",
        help=(
            "also use OCR on pages whose text layer is present but misordered "
            "(RTL_VISUAL_ORDER, COMBINING_MARK_ORDER); native text is kept in "
            "diagnostic_elements. Requires --ocr"
        ),
    )
    parser.add_argument(
        "--ocr-min-confidence",
        type=ocr_confidence,
        default=0.4,
        metavar="N",
        help="drop recognised lines below this score (0-1, default 0.4)",
    )
    parser.add_argument(
        "--tables",
        action="store_true",
        help=(
            "detect tables. Uses line geometry, which needs nothing extra; "
            "Table Transformer instead when the model stack is installed"
        ),
    )
    parser.add_argument(
        "--layout",
        action="store_true",
        help=(
            "detect layout regions with DocLayNet and give each line a semantic "
            "type (caption, section_header, ...). Also enables alignment"
        ),
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

    # Many inputs need a directory, or only the last survives.
    many = len(args.pdfs) > 1
    if many and args.output and not args.output.is_dir():
        if args.output.exists():
            parser.error(f"{args.output} is a file; give a directory for several PDFs")
        args.output.mkdir(parents=True, exist_ok=True)
    # One input written to an existing directory: caught here rather than as an
    # IsADirectoryError traceback from write_text() outside the try block.
    if not many and args.format not in TREE_FORMATS and args.output and args.output.is_dir():
        parser.error(f"{args.output} is a directory; give a file path, or use -f bundle")

    if args.ocr_repair and not args.ocr:
        parser.error("--ocr-repair needs --ocr LANG to say which language to recognise")

    ocr = None
    if args.ocr:
        # Imported here, not at module scope: the OCR stack is optional, and a
        # missing pytesseract must not stop the CLI running without --ocr.
        from docyx.analysis.detectors.tesseract import TesseractDetector
        from docyx.analysis.ocr import OCRAnalyzer

        try:
            detector = TesseractDetector(lang=args.ocr)
        except ImportError as exc:
            parser.error(str(exc))

        # Checked here rather than left to the page. A language tesseract does
        # not have raises per page, which `_safely` turns into STAGE_FAILED and
        # the summary prints as `1 failed` — so a missing 5 MB data file reads
        # as "this scan is unreadable" instead of "install ben.traineddata".
        installed = set(detector.languages())
        missing = sorted(set(args.ocr.split("+")) - installed)
        if missing:
            parser.error(
                f"tesseract has no data for {', '.join(missing)}. "
                f"Installed: {', '.join(sorted(installed)) or 'none'}. "
                "Install it with your package manager, or download the file and "
                "point TESSDATA_PREFIX at it — see requirements-ocr.txt."
            )

        ocr = OCRAnalyzer(detector=detector, min_confidence=args.ocr_min_confidence)

    # Lazy: the model stack is optional.
    layout_analyzer = table_analyzer = None
    try:
        if args.layout:
            from docyx.analysis.detectors.doclaynet import DocLayNetDetector
            from docyx.analysis.layout import LayoutAnalyzer

            layout_analyzer = LayoutAnalyzer(detector=DocLayNetDetector())
        if args.tables:
            # The model is preferred only because it had the page image and so
            # gets the first say; measured, it does not beat the geometry pass
            # (scripts/measure_tables.py). Its absence is not an error.
            try:
                from docyx.analysis.detectors.table_transformer import (
                    TableTransformerDetector,
                )
                from docyx.analysis.tables import TableAnalyzer

                table_analyzer = TableAnalyzer(detector=TableTransformerDetector())
            except ImportError:
                pass
    except ImportError as exc:
        parser.error(str(exc))

    pipeline = DocyxPipeline(
        layout_analyzer=layout_analyzer,
        table_analyzer=table_analyzer,
        ocr_analyzer=ocr,
        ocr_repair=args.ocr_repair,
        table_geometry=args.tables,
    )
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

        if args.format in TREE_FORMATS:
            # A tree cannot go to stdout.
            if args.output is None:
                parser.error(f"-f {args.format} writes a directory tree, so -o DIR is required")
            target = args.output / pdf.stem if many else args.output
            write = write_bundle if args.format == "bundle" else write_blocks
            paths = write(document, str(pdf), target)
            if not args.quiet:
                print(f"{target}: {len(paths)} files", file=sys.stderr)
        else:
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
