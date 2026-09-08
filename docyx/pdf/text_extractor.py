import fitz
from typing import List

from docyx.core.constants import SCALE
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Element, Typography


class NativeTextExtractor:
    def __init__(self, doc: fitz.Document):
        self.doc = doc

    def extract_page(self, page_num: int) -> List[Element]:
        page = self.doc[page_num]
        blocks = page.get_text("dict").get("blocks", [])
        elements = []
        for b_idx, b in enumerate(blocks):
            if b.get("type", -1) == 0:  # text block
                for l_idx, l in enumerate(b.get("lines", [])):
                    for s_idx, s in enumerate(l.get("spans", [])):
                        text = s.get("text", "")
                        if not text.strip():
                            continue

                        x0, y0, x1, y1 = s["bbox"]
                        bbox = BoundingBox(
                            x=x0 * SCALE,
                            y=y0 * SCALE,
                            width=(x1 - x0) * SCALE,
                            height=(y1 - y0) * SCALE,
                        )
                        geom = Geometry(bbox=bbox)
                        conf = Confidence(value=1.0, type=ConfidenceType.EXACT)
                        prov = Provenance(source=ProvenanceSource.NATIVE_PDF, engine="PyMuPDF")

                        el = Element(
                            # Positional, so the same PDF always exports the same ids.
                            id=f"page{page_num+1}_b{b_idx}_l{l_idx}_s{s_idx}",
                            type="text",
                            geometry=geom,
                            confidence=conf,
                            provenance=prov,
                            text=text,
                            typography=Typography(
                                font_family=s.get("font"),
                                # Point size, NOT scaled to 150 DPI — points are the
                                # unit typography is authored and reasoned about in.
                                font_size=s.get("size"),
                                flags=s.get("flags"),
                                color=s.get("color"),
                            ),
                        )
                        elements.append(el)
        return elements
