import fitz
from typing import Any, Dict, List, Optional

from docyx.core.constants import SCALE
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Direction, Element, TextLayout, Typography, script_of


class NativeTextExtractor:
    """Extracts native text at line granularity (§5).

    Spans are joined back into their line: PyMuPDF emits inter-word gaps as
    their own spans, so plain concatenation reproduces the line exactly, which
    is why blank spans must not be skipped.
    """

    def __init__(self, doc: fitz.Document):
        self.doc = doc

    def extract_page(self, page_num: int) -> List[Element]:
        page = self.doc[page_num]
        elements: List[Element] = []

        for b_idx, block in enumerate(page.get_text("dict").get("blocks", [])):
            if block.get("type", -1) != 0:  # not a text block
                continue
            block_lines: List[Element] = []
            for l_idx, line in enumerate(block.get("lines", [])):
                spans = line.get("spans", [])
                # Whitespace-only spans carry the gaps between words — dropping
                # them here would run the words together.
                text = "".join(s.get("text", "") for s in spans)
                if not text.strip():
                    continue

                # Positional, so the same PDF always exports the same ids.
                element_id = f"page{page_num + 1}_b{b_idx}_l{l_idx}"
                element = Element(
                    id=element_id,
                    type="text",
                    geometry=Geometry(bbox=_bbox(line["bbox"])),
                    confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
                    provenance=Provenance(source=ProvenanceSource.NATIVE_PDF, engine="PyMuPDF"),
                    text=text,
                    typography=_dominant_typography(spans),
                    direction=_direction(line, spans),
                    script=script_of(text),
                    children=_style_runs(spans, element_id),
                )
                block_lines.append(element)

            # Measured once every line in the block exists.
            # A malformed block may carry no bbox of its own.
            if not block_lines:
                continue
            block_box = (
                _bbox(block["bbox"])
                if "bbox" in block
                else _union_all([el.geometry.bbox for el in block_lines])
            )
            for line_element in block_lines:
                line_element.layout = _layout(line_element, block_lines, block_box)
            elements.extend(block_lines)
        return elements


def _layout(element: Element, siblings: List[Element], block_box: BoundingBox) -> TextLayout:
    """Indent and line height, measured against the block.

    Alignment is not computed here: a PyMuPDF block is not a paragraph, so the
    measurement is wrong. The pipeline computes it against layout regions.
    """
    box = element.geometry.bbox
    rtl = element.direction is Direction.RTL

    # The NEXT line in the block, by position rather than by list order.
    below = [s for s in siblings if s.geometry.bbox.y > box.y + 1]
    nearest = min(below, key=lambda s: s.geometry.bbox.y, default=None)

    return TextLayout(
        # From the leading edge: a right-to-left paragraph indents from the
        # right, and reporting its left gap would invert the meaning.
        indent=round((block_box.x1 - box.x1) if rtl else (box.x - block_box.x), 2),
        line_height=round(nearest.geometry.bbox.y - box.y, 2) if nearest else None,
    )


def _style_runs(spans: List[Dict[str, Any]], line_id: str) -> List[Element]:
    """Style runs for a line that mixes styles; empty for a uniform line.

    The line's own `typography` is its dominant span, which loses a short bold
    run. Uniform lines get no children, which would otherwise double the
    output to restate what the line already says.
    """
    scoring = [s for s in spans if s.get("text", "").strip()]
    styles = {(s.get("flags"), s.get("size"), s.get("font")) for s in scoring}
    if len(styles) < 2:
        return []

    runs: List[Element] = []
    for span in spans:
        text = span.get("text", "")
        if not text.strip():
            continue
        typography = _typography(span)
        if runs and runs[-1].typography == typography:
            # PyMuPDF splits on things a reader cannot see; merge them.
            runs[-1].text = (runs[-1].text or "") + text
            runs[-1].geometry.bbox = _union(runs[-1].geometry.bbox, _bbox(span["bbox"]))
            continue
        runs.append(
            Element(
                id=f"{line_id}_s{len(runs)}",
                type="text_span",
                geometry=Geometry(bbox=_bbox(span["bbox"])),
                confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
                provenance=Provenance(source=ProvenanceSource.NATIVE_PDF, engine="PyMuPDF"),
                text=text,
                typography=typography,
                script=script_of(text),
            )
        )
    return runs


def _union_all(boxes: List[BoundingBox]) -> BoundingBox:
    box = boxes[0]
    for other in boxes[1:]:
        box = _union(box, other)
    return box


def _union(a: BoundingBox, b: BoundingBox) -> BoundingBox:
    x, y = min(a.x, b.x), min(a.y, b.y)
    return BoundingBox(x=x, y=y, width=max(a.x1, b.x1) - x, height=max(a.y1, b.y1) - y)


def _typography(span: Dict[str, Any]) -> Typography:
    return Typography(
        font_family=span.get("font"),
        font_size=span.get("size"),
        flags=span.get("flags"),
        color=span.get("color"),
    )


def _bbox(rect) -> BoundingBox:
    x0, y0, x1, y1 = rect
    return BoundingBox(
        x=x0 * SCALE,
        y=y0 * SCALE,
        width=(x1 - x0) * SCALE,
        height=(y1 - y0) * SCALE,
    )


def _dominant_typography(spans: List[Dict[str, Any]]) -> Optional[Typography]:
    """Typography of the span covering the most non-blank characters."""
    scoring = [s for s in spans if s.get("text", "").strip()]
    if not scoring:
        return None
    # Size stays in points, not scaled to 150 DPI.
    return _typography(max(scoring, key=lambda s: len(s.get("text", "").strip())))


def _direction(line: Dict[str, Any], spans: List[Dict[str, Any]]) -> Direction:
    """Writing direction (§13): the line's vector for vertical, characters
    for LTR vs RTL."""
    dx, dy = line.get("dir", (1.0, 0.0))
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return Direction.UNKNOWN
    if abs(dy) > abs(dx):
        return Direction.TTB

    return Direction.of_text("".join(s.get("text", "") for s in spans))
