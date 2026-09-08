import fitz

from docyx.core.constants import SCALE


class PDFRenderer:
    def __init__(self, file_path_or_stream):
        if isinstance(file_path_or_stream, str):
            self.doc = fitz.open(file_path_or_stream)
        else:
            self.doc = fitz.open(stream=file_path_or_stream, filetype="pdf")

    def render_page(self, page_num: int) -> bytes:
        page = self.doc[page_num]
        mat = fitz.Matrix(SCALE, SCALE)
        pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("png")

    def close(self) -> None:
        self.doc.close()

    def __enter__(self) -> "PDFRenderer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
