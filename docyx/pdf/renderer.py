import fitz

class PDFRenderer:
    def __init__(self, file_path_or_stream):
        if isinstance(file_path_or_stream, str):
            self.doc = fitz.open(file_path_or_stream)
        else:
            self.doc = fitz.open(stream=file_path_or_stream, filetype="pdf")

    def render_page(self, page_num: int) -> bytes:
        page = self.doc[page_num]
        zoom = 150 / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        return pix.tobytes("png")
