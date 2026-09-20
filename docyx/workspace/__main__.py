"""python -m docyx.workspace (document.pdf | folder/) [--layout] [--tables] [--ocr LANG]"""

import argparse

from docyx.workspace.server import serve


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="docyx.workspace",
        description="Look at what Docyx extracted, drawn over the rendered page.",
    )
    parser.add_argument("pdf", metavar="PDF_OR_FOLDER",
                        help="a PDF, or a folder of them to step through")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--layout", action="store_true", help="detect layout regions")
    parser.add_argument("--tables", action="store_true", help="detect table structure")
    parser.add_argument("--ocr", metavar="LANG", help="recognise gate-failed pages")
    args = parser.parse_args(argv)

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
                from docyx.analysis.detectors.table_transformer import TableTransformerDetector
                from docyx.analysis.tables import TableAnalyzer

                tables = TableAnalyzer(detector=TableTransformerDetector())
            if args.ocr:
                from docyx.analysis.detectors.tesseract import TesseractDetector
                from docyx.analysis.ocr import OCRAnalyzer

                ocr = OCRAnalyzer(detector=TesseractDetector(lang=args.ocr), min_confidence=0.4)
        except ImportError as exc:
            parser.error(str(exc))
        pipeline = DocyxPipeline(
            layout_analyzer=layout, table_analyzer=tables, ocr_analyzer=ocr
        )

    try:
        serve(args.pdf, port=args.port, open_browser=not args.no_browser,
              pipeline=pipeline)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
