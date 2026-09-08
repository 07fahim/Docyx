from typing import Union

from docyx.core.constants import SCALE
from docyx.pdf.renderer import PDFRenderer
from docyx.pdf.text_extractor import NativeTextExtractor
from docyx.pipeline.gate import TextLayerGate
from docyx.schema.models import Document, Page, PageStatus


class DocyxPipeline:
    def __init__(self):
        self.gate = TextLayerGate()

    def process(self, file_path_or_stream: Union[str, bytes], document_id: str) -> Document:
        renderer = PDFRenderer(file_path_or_stream)
        try:
            extractor = NativeTextExtractor(renderer.doc)
            doc_model = Document(document_id=document_id, pages=[])

            for page_num in range(len(renderer.doc)):
                gate_result = self.gate.check_page(renderer.doc, page_num)

                # Render unconditionally; downstream stages never depend on the gate.
                _ = renderer.render_page(page_num)

                rect = renderer.doc[page_num].rect
                page_model = Page(
                    page_number=page_num + 1,
                    status=PageStatus.OK if gate_result.passed else PageStatus.FAILED,
                    width=int(rect.width * SCALE),
                    height=int(rect.height * SCALE),
                )

                if gate_result.passed:
                    page_model.elements = extractor.extract_page(page_num)
                else:
                    page_model.errors.append(gate_result.error)

                doc_model.pages.append(page_model)

            return doc_model
        finally:
            renderer.close()
