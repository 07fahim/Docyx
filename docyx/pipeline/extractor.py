import fitz
from typing import Union
from docyx.schema.models import Document, Page, PageStatus
from docyx.pipeline.gate import TextLayerGate
from docyx.pdf.renderer import PDFRenderer
from docyx.pdf.text_extractor import NativeTextExtractor

class DocyxPipeline:
    def __init__(self):
        self.gate = TextLayerGate()

    def process(self, file_path_or_stream: Union[str, bytes], document_id: str) -> Document:
        renderer = PDFRenderer(file_path_or_stream)
        extractor = NativeTextExtractor(renderer.doc)
        doc_model = Document(document_id=document_id, pages=[])
        
        for page_num in range(len(renderer.doc)):
            gate_result = self.gate.check_page(file_path_or_stream, page_num)
            
            # Render Page unconditionally (output can be cached/saved as needed by downstream)
            _ = renderer.render_page(page_num)
            
            fitz_page = renderer.doc[page_num]
            width = int(fitz_page.rect.width * (150 / 72))
            height = int(fitz_page.rect.height * (150 / 72))
            
            page_model = Page(
                page_number=page_num + 1,
                status=PageStatus.OK if gate_result.passed else PageStatus.FAILED,
                width=width,
                height=height,
            )
            
            if not gate_result.passed:
                page_model.errors.append(gate_result.error.model_dump_json())
            else:
                page_model.elements = extractor.extract_page(page_num)
                
            doc_model.pages.append(page_model)
            
        return doc_model
