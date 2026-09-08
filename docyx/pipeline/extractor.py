import fitz
from typing import Optional, Union

from docyx.analysis.layout import LayoutAnalyzer
from docyx.analysis.reading_order import ReadingOrderCalculator
from docyx.analysis.tables import TableAnalyzer
from docyx.pdf.renderer import PDFRenderer
from docyx.pdf.text_extractor import NativeTextExtractor
from docyx.pipeline.gate import TextLayerGate
from docyx.schema.models import Document, Page, PageStatus


class DocyxPipeline:
    def __init__(
        self,
        layout_analyzer: Optional[LayoutAnalyzer] = None,
        table_analyzer: Optional[TableAnalyzer] = None,
    ):
        self.gate = TextLayerGate()
        self.layout_analyzer = layout_analyzer or LayoutAnalyzer()
        self.table_analyzer = table_analyzer or TableAnalyzer()

    def process(self, file_path_or_stream: Union[str, bytes], document_id: str) -> Document:
        renderer = PDFRenderer(file_path_or_stream)
        extractor = NativeTextExtractor(renderer.doc)
        doc_model = Document(document_id=document_id, pages=[])

        for page_num in range(len(renderer.doc)):
            gate_result = self.gate.check_page(file_path_or_stream, page_num)

            # Render page image unconditionally — layout/table detection never depends on the gate.
            image_bytes = renderer.render_page(page_num)

            # Visual detection runs on every page regardless of the gate outcome.
            layout_elements = self.layout_analyzer.analyze(image_bytes, page_num=page_num)
            table_elements = self.table_analyzer.analyze(image_bytes, page_num=page_num)

            fitz_page = renderer.doc[page_num]
            width = int(fitz_page.rect.width * (150 / 72))
            height = int(fitz_page.rect.height * (150 / 72))

            if gate_result.passed:
                elements = extractor.extract_page(page_num) + layout_elements + table_elements
                elements = ReadingOrderCalculator.calculate(elements)
                page_model = Page(
                    page_number=page_num + 1,
                    status=PageStatus.OK,
                    width=width,
                    height=height,
                    elements=elements,
                )
            else:
                page_model = Page(
                    page_number=page_num + 1,
                    status=PageStatus.FAILED,
                    width=width,
                    height=height,
                    errors=[gate_result.error.model_dump_json()],
                    diagnostic_elements=layout_elements + table_elements,
                )

            doc_model.pages.append(page_model)

        return doc_model