import fitz

from docyx.core.constants import SCALE


class PDFRenderer:
    def __init__(self, file_path_or_stream):
        if isinstance(file_path_or_stream, str):
            self.doc = fitz.open(file_path_or_stream)
        else:
            self.doc = fitz.open(stream=file_path_or_stream, filetype="pdf")

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
