from pathlib import Path

import fitz

from docyx.core.constants import SCALE


class PDFRenderer:
    def __init__(self, file_path_or_stream):
        if isinstance(file_path_or_stream, (str, Path)):
            self.doc = fitz.open(str(file_path_or_stream))
        else:
            self.doc = fitz.open(stream=file_path_or_stream, filetype="pdf")

        # PyMuPDF opens XPS, EPUB, CBZ and Office documents too, so `open`
        # succeeding is not evidence of a PDF. v1 is PDF-only, and a .docx went
        # through the pipeline reported as "1 ok" — every coordinate, every
        # provenance claim and the schema's meaning assume a PDF page, so a
        # silent wrong answer is the worst outcome available here.
        if not self.doc.is_pdf:
            fmt = (self.doc.metadata or {}).get("format", "unknown")
            self.doc.close()
            raise ValueError(
                f"input is not a PDF (PyMuPDF reports {fmt!r}); "
                "v1 accepts PDF only"
            )

        # A PDF with no pages opens cleanly, reports is_pdf, and is not
        # encrypted — it simply has nothing in it. Found on a real 14 MB
        # Arabic government report whose page tree does not resolve. Left
        # alone, `process()` returns a Document with zero pages and no error,
        # and the CLI exits 0 because "every page produced a valid result" is
        # vacuously true of no pages. Silently reporting success for a file
        # that produced nothing is the worst outcome available here.
        if len(self.doc) == 0:
            self.doc.close()
            raise ValueError(
                "PDF contains no pages; the file is damaged or its page tree "
                "could not be read"
            )

    def page_count(self) -> int:
        """Declared here so callers need not reach through to the fitz document."""
        return len(self.doc)

    def has_images(self, page_num: int) -> bool:
        """Does the page carry raster content? Distinguishes a scan from a
        genuinely blank page, both of which fail the text-layer gate."""
        return bool(self.doc[page_num].get_images())

    def render_page(self, page_num: int) -> bytes:
        return self._pixmap(page_num).tobytes("png")

    def page_size(self, page_num: int) -> tuple:
        """Rendered extent in 150-DPI pixels.

        Taken from the pixmap rather than computed as int(points * SCALE):
        rendering rounds where int() truncates, so A4 reported 1239x1754 for an
        image that is actually 1240x1755. Element coordinates live in pixmap
        space, so a box on the right margin could exceed the page's own
        declared width — which the phase-5 overlay would show as drift.
        """
        pix = self._pixmap(page_num)
        return pix.width, pix.height

    def _pixmap(self, page_num: int):
        return self.doc[page_num].get_pixmap(matrix=fitz.Matrix(SCALE, SCALE))

    def close(self) -> None:
        self.doc.close()

    def __enter__(self) -> "PDFRenderer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
