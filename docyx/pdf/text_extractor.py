import fitz
from typing import Any, Dict, List, Optional

from docyx.core.constants import SCALE
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Element, Typography


class NativeTextExtractor:
    """Extracts native text at **line** granularity.

    §5: "line or block granularity is the recommended default for v1". This
    previously emitted one element per *span*, which splits a line wherever the
    font changes — at every inline citation, superscript and piece of maths.
    Since reading order sorts on (y, x) and those fragments sit on slightly
    different baselines, they scattered: "We employ a residual connection [ ]
    around each of 11 the two sub-layers".

    Joining spans back into their line fixes it at the source. PyMuPDF emits the
    inter-word spaces as their own spans, so concatenation reproduces the line
    exactly — no gap detection or space insertion needed, and no assumption
    about writing direction, since spans arrive in logical order (which keeps
    RTL correct).

    Per-span typography is collapsed to whichever span covers the most
    characters. If a downstream consumer ever needs sub-line detail, spans
    belong in Element.children rather than back at the top level.
    """

    def __init__(self, doc: fitz.Document):
        self.doc = doc

    def extract_page(self, page_num: int) -> List[Element]:
        page = self.doc[page_num]
        elements: List[Element] = []

        for b_idx, block in enumerate(page.get_text("dict").get("blocks", [])):
            if block.get("type", -1) != 0:  # not a text block
                continue
            for l_idx, line in enumerate(block.get("lines", [])):
                spans = line.get("spans", [])
                # Whitespace-only spans carry the gaps between words — dropping
                # them here would run the words together.
                text = "".join(s.get("text", "") for s in spans)
                if not text.strip():
                    continue

                element = Element(
                    # Positional, so the same PDF always exports the same ids.
                    id=f"page{page_num + 1}_b{b_idx}_l{l_idx}",
                    type="text",
                    geometry=Geometry(bbox=_bbox(line["bbox"])),
                    confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
                    provenance=Provenance(source=ProvenanceSource.NATIVE_PDF, engine="PyMuPDF"),
                    text=text,
                    typography=_dominant_typography(spans),
                )
                elements.append(element)
        return elements


def _bbox(rect) -> BoundingBox:
    x0, y0, x1, y1 = rect
    return BoundingBox(
        x=x0 * SCALE,
        y=y0 * SCALE,
        width=(x1 - x0) * SCALE,
        height=(y1 - y0) * SCALE,
    )


def _dominant_typography(spans: List[Dict[str, Any]]) -> Optional[Typography]:
    """Typography of the span covering the most non-blank characters.

    A line is usually one font; where it is not, the body font is the honest
    summary — taking the first span would let a leading superscript or drop cap
    misreport the whole line, and the heading heuristic keys on font size.
    """
    scoring = [s for s in spans if s.get("text", "").strip()]
    if not scoring:
        return None
    span = max(scoring, key=lambda s: len(s.get("text", "").strip()))
    return Typography(
        font_family=span.get("font"),
        # Point size, NOT scaled to 150 DPI — points are the unit typography is
        # authored and reasoned about in.
        font_size=span.get("size"),
        flags=span.get("flags"),
        color=span.get("color"),
    )
