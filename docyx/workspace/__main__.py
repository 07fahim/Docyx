"""python -m docyx.workspace (document.pdf | folder/) [--layout] [--tables] [--ocr LANG]"""

import argparse
from pathlib import Path

from docyx.cli import ocr_language
from docyx.workspace.server import serve


def _port(value: str) -> int:
    """A bad port otherwise surfaces as an OverflowError from socket.bind."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number")
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError(
            f"{port} is outside 1024-65535; below 1024 also needs root"
        )
    return port


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="docyx.workspace",
        description="Look at what Docyx extracted, drawn over the rendered page.",
    )
    parser.add_argument("pdf", metavar="PDF_OR_FOLDER",
                        help="a PDF, or a folder of them to step through")
    parser.add_argument("--port", type=_port, default=8000,
                        help="1024-65535; default 8000")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--layout", action="store_true", help="detect layout regions")
    parser.add_argument(
        "--tables",
        action="store_true",
        help=(
            "detect tables. Uses line geometry, which needs nothing extra; "
            "Table Transformer instead when the model stack is installed"
        ),
    )
    parser.add_argument("--ocr", metavar="LANG", type=ocr_language,
                        help="recognise gate-failed pages, e.g. ben, ara, ben+eng")
    args = parser.parse_args(argv)

    # Checked before anything is built: PyMuPDF otherwise raises its own
    # FileNotFoundError as a traceback, where the main CLI says "not a file".
    if not Path(args.pdf).exists():
        parser.error(f"{args.pdf}: no such file or folder")

    pipeline = None
    if args.layout or args.tables or args.ocr:
        from docyx.pipeline.extractor import DocyxPipeline

        layout = tables = ocr = None
        try:
            if args.layout:
                from docyx.analysis.detectors.doclaynet import DocLayNetDetector
                from docyx.analysis.layout import LayoutAnalyzer

                layout = LayoutAnalyzer(detector=DocLayNetDetector())
            if args.tables:
                # Same contract as the main CLI: the model is preferred only
                # because it had the page image, and its absence is not an
                # error -- the geometry pass covers it.
                try:
                    from docyx.analysis.detectors.table_transformer import (
                        TableTransformerDetector,
                    )
                    from docyx.analysis.tables import TableAnalyzer

                    tables = TableAnalyzer(detector=TableTransformerDetector())
                except ImportError:
                    pass
            if args.ocr:
                from docyx.analysis.detectors.tesseract import TesseractDetector
                from docyx.analysis.ocr import OCRAnalyzer

                detector = TesseractDetector(lang=args.ocr)
                # Same reason as the main CLI: a missing language file
                # otherwise fails every page and reads as an unreadable PDF.
                missing = sorted(set(args.ocr.split("+")) - set(detector.languages()))
                if missing:
                    parser.error(
                        f"tesseract has no data for {', '.join(missing)}. "
                        f"Installed: {', '.join(sorted(detector.languages())) or 'none'}."
                    )
                ocr = OCRAnalyzer(detector=detector, min_confidence=0.4)
        except ImportError as exc:
            parser.error(str(exc))
        pipeline = DocyxPipeline(
            layout_analyzer=layout,
            table_analyzer=tables,
            ocr_analyzer=ocr,
            table_geometry=args.tables,
        )

    try:
        serve(args.pdf, port=args.port, open_browser=not args.no_browser,
              pipeline=pipeline)
    except ValueError as exc:
        parser.error(str(exc))
    except OSError as exc:
        # An address already in use is the common one, and a traceback for it
        # tells the user nothing they can act on.
        parser.error(f"could not serve on port {args.port}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
