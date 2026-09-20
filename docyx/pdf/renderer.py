from pathlib import Path
from typing import Optional

import fitz

from docyx.core.constants import SCALE
from docyx.pdf.protocols import TextDocument
from docyx.pdf.text_extractor import NativeTextExtractor


class PDFRenderer:
    def __init__(self, file_path_or_stream):
        if isinstance(file_path_or_stream, (str, Path)):
            self.doc = fitz.open(str(file_path_or_stream))
        else:
            self.doc = fitz.open(stream=file_path_or_stream, filetype="pdf")

        # PyMuPDF also opens XPS, EPUB and Office documents, so `open`
        # succeeding is not evidence of a PDF.
        if not self.doc.is_pdf:
            fmt = (self.doc.metadata or {}).get("format", "unknown")
            self.doc.close()
            raise ValueError(
                f"input is not a PDF (PyMuPDF reports {fmt!r}); "
                "v1 accepts PDF only"
            )

        # A password-protected PDF also opens cleanly, and then every page
        # raises. Left alone that reported `1 failed [PAGE_UNREADABLE]`, which
        # reads as a damaged file rather than one nobody supplied the password
        # for — so the user chases the wrong problem. This is the same class as
        # the zero-page case below: rejected at the boundary, where the reason
        # is still known.
        if self.doc.needs_pass:
            self.doc.close()
            raise ValueError(
                "PDF is password-protected; Docyx does not accept passwords, "
                "so decrypt it first"
            )

        # A zero-page PDF opens cleanly. Left alone it returns a Document
        # with no pages and no error, and the CLI exits 0.
        if len(self.doc) == 0:
            self.doc.close()
            raise ValueError(
                "PDF contains no pages; the file is damaged or its page tree "
                "could not be read"
            )

        self._cached_page: Optional[int] = None
        self._cached = None

    def permits_extraction(self) -> bool:
        """Does the document's own permission bitfield allow copying text?

        An owner password restricts permissions without restricting access, so
        this is a claim the file makes rather than a lock it enforces -- and
        essentially every tool ignores it. Docyx reports it instead of either
        obeying it silently or ignoring it silently, which is the same contract
        it applies to a text layer that lies.
        """
        return bool(self.doc.permissions & fitz.PDF_PERM_COPY)

    def page_count(self) -> int:
        """Declared here so callers need not reach through to the fitz document."""
        return len(self.doc)

    def text_document(self) -> TextDocument:
        """The page-text surface, typed as the protocol rather than as fitz."""
        return self.doc

    def text_extractor(self) -> NativeTextExtractor:
        """Built here so the fitz document never leaves this package."""
        return NativeTextExtractor(self.doc)

    def image_rects(self, page_num: int) -> list:
        """Where the page declares its raster images, in 150 DPI pixels.

        Ground truth, unlike the figure heuristic: the file says where its
        pictures are. Used to suppress "rules" that are really a strong edge
        inside a photograph — a lit facade, a horizon, the image's own frame.

        A raster covering nearly the whole page is a scan rather than a figure,
        and a scanned form's ruled table borders are real rules, so those are
        excluded. Returns plain tuples: `fitz.Rect` must not leave this package.
        """
        page = self.doc[page_num]
        page_area = abs(page.rect.width * page.rect.height) or 1.0
        out = []
        for info in page.get_images(full=True):
            for rect in page.get_image_rects(info[0]):
                if abs(rect.width * rect.height) / page_area >= 0.8:
                    continue
                out.append((rect.x0 * SCALE, rect.y0 * SCALE,
                            rect.x1 * SCALE, rect.y1 * SCALE))
        return out

    def has_images(self, page_num: int) -> bool:
        """Does the page carry raster content? Distinguishes a scan from a
        genuinely blank page, both of which fail the text-layer gate."""
        return bool(self.doc[page_num].get_images())

    def render_page(self, page_num: int) -> bytes:
        return self._pixmap(page_num).tobytes("png")

    def page_size(self, page_num: int) -> tuple:
        """Rendered extent in 150-DPI pixels.

        From the pixmap, not int(points * SCALE): rendering rounds where int()
        truncates, so a box on the right margin could exceed the page width.
        """
        pix = self._pixmap(page_num)
        return pix.width, pix.height

    def _pixmap(self, page_num: int):
        # Cached: _process_page calls render_page() and page_size() for the
        # same page, which otherwise rasterises it twice at 150 DPI, and
        # rendering dominates the per-page cost.
        if self._cached_page != page_num:
            self._cached = self.doc[page_num].get_pixmap(matrix=fitz.Matrix(SCALE, SCALE))
            self._cached_page = page_num
        return self._cached

    def close(self) -> None:
        self.doc.close()

    def __enter__(self) -> "PDFRenderer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
