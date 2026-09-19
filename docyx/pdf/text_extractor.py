import fitz
from typing import Any, Dict, List, Optional

from docyx.core.constants import SCALE
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Direction, Element, TextLayout, Typography, script_of


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

            # Layout is relative to the BLOCK, so it can only be measured once
            # every line in the block exists.
            # Fall back to the union of the lines: a fake block in a test, or
            # a malformed one in the wild, may carry no bbox of its own.
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


# A line counts as flush with its block's edge within this many 150-DPI pixels.
# Justified text is not pixel-perfect and a glyph's advance width overshoots
# slightly, so an exact comparison would report almost nothing as flush.
FLUSH_TOLERANCE = 4.0


def _layout(element: Element, siblings: List[Element], block_box: BoundingBox) -> TextLayout:
    """Where this line sits inside its block (§7).

    `indent` and `line_height` only. Both are plain measurements against the
    block and are reported whenever they exist.

    `alignment` is deliberately NOT computed here, and that cost two attempts
    to establish. A PyMuPDF block is not a paragraph: on arxiv_attention p2 one
    block holds "3.1" and "Encoder and Decoder Stacks" together, and another
    holds a bold run-in label plus the sentence after it. Measured against the
    block bbox, the heading came out `right`; measured against the block's
    modal edges, a short last line came out `justify` and body text came out
    `right`. Both rules were confidently wrong on a real page.

    Alignment needs a real paragraph box, which only a layout model supplies —
    so it is computed in the pipeline (`_assign_alignment`) where regions are
    available, and left None otherwise. An absent value is worth more than a
    wrong one in a schema whose whole claim is that values can be trusted.
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
    """Sub-line runs, but only when the line is not all one style.

    A line's `typography` is its DOMINANT span, which is the honest summary of
    a uniform line and a lossy one otherwise: "**Note:** and the rest of this
    sentence" reports `bold: false`, because the normal run is longer, and the
    bold disappears. Measured on the corpus, 3.6% of lines mix bold with
    non-bold and 9.0% mix any two styles — small, but not a rounding error, and
    invisible to a consumer who only sees the line.

    Emitted ONLY for mixed lines. Giving every uniform line a child that
    restates its own typography would roughly double the output to say nothing;
    an empty `children` means "the line's own typography is the whole story".

    Runs are merged where adjacent spans share a style, because PyMuPDF splits
    on things a reader does not see — a citation bracket, a kerning pair — and
    those are not style changes.

    These are NOT orderable: `text_span` is absent from TEXT_ROLES, and they
    live in `children` rather than at the top level, so reading order never
    numbers a line and its own fragments as separate positions.
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
            # Same style as the previous run — extend it rather than emitting
            # a second element for a split the reader cannot see.
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
    """Typography of the span covering the most non-blank characters.

    A line is usually one font; where it is not, the body font is the honest
    summary — taking the first span would let a leading superscript or drop cap
    misreport the whole line, and the heading heuristic keys on font size.
    """
    scoring = [s for s in spans if s.get("text", "").strip()]
    if not scoring:
        return None
    # Point size stays in points, NOT scaled to 150 DPI — points are the unit
    # typography is authored and reasoned about in.
    return _typography(max(scoring, key=lambda s: len(s.get("text", "").strip())))


def _direction(line: Dict[str, Any], spans: List[Dict[str, Any]]) -> Direction:
    """Writing direction, read from the PDF rather than inferred (§13).

    The line's ``dir`` is a unit vector: (1, 0) for ordinary horizontal text,
    (0, +/-1) once the text is set vertically.

    Horizontal direction is decided by the Unicode bidi category of the
    characters, NOT by the span's ``bidi`` embedding level. Measured on a real
    Arabic PDF (`.corpus/wiki_ar.pdf`): every span reports ``bidi=0``, because
    the generator laid the glyphs out visually and discarded the levels. That
    is normal — PDF is a presentation format — and trusting ``bidi`` classified
    all 74 elements on an Arabic page as LTR, leaving the RTL column-ordering
    path dead on exactly the documents it was written for. A character cannot
    misreport its own script.
    """
    dx, dy = line.get("dir", (1.0, 0.0))
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return Direction.UNKNOWN
    if abs(dy) > abs(dx):
        return Direction.TTB

    # Only the vertical case needs the PDF's own direction vector; the
    # left-to-right vs right-to-left question is answered by the characters,
    # and OCR asks it of the same helper.
    return Direction.of_text("".join(s.get("text", "") for s in spans))
