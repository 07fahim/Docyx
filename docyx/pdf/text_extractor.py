import fitz
from typing import List
from docyx.schema.models import Element
from docyx.core.geometry import Geometry, BoundingBox
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource

class NativeTextExtractor:
    def __init__(self, doc: fitz.Document):
        self.doc = doc
        self.scale = 150 / 72

    def extract_page(self, page_num: int) -> List[Element]:
        page = self.doc[page_num]
        blocks = page.get_text("dict").get("blocks", [])
        elements = []
        for b_idx, b in enumerate(blocks):
            if b.get("type", -1) == 0:  # text block
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        text = s.get("text", "")
                        if not text.strip():
                            continue
                            
                        x0, y0, x1, y1 = s["bbox"]
                        bbox = BoundingBox(
                            x=x0 * self.scale,
                            y=y0 * self.scale,
                            width=(x1 - x0) * self.scale,
                            height=(y1 - y0) * self.scale
                        )
                        geom = Geometry(bbox=bbox)
                        conf = Confidence(value=1.0, type=ConfidenceType.EXACT)
                        prov = Provenance(source=ProvenanceSource.NATIVE_PDF, engine="PyMuPDF")
                        
                        el = Element(
                            id=f"page{page_num+1}_b{b_idx}_{hash(text)}",
                            type="text",
                            geometry=geom,
                            confidence=conf,
                            provenance=prov,
                            text=text
                        )
                        elements.append(el)
        return elements
