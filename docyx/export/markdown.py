"""Reconstruct readable Markdown from a page representation.

This is deliberately two things at once:

1. A feature. Plenty of consumers want prose, not a JSON element tree.
2. An evaluation instrument. Reading order and layout classification are hard
   to judge from metrics — DocLayNet mAP tells you about boxes, not about
   whether the *document* came out right. Scrambled Markdown is instantly
   visible to a human, which is the fastest signal available that reading
   order is wrong.

Headings are inferred from font size, because layout classification is
currently a stub: the most common size on a page is body text, and anything
meaningfully larger is a heading, ranked descending into levels. That is a
heuristic and it is wrong on documents that signal hierarchy by weight or
colour alone. When a real layout model (or the §10 struct tree) lands, its
element types should take precedence and this should become the fallback.
"""

from collections import Counter
from typing import Dict, List, Optional, Tuple

from docyx.core.geometry import BoundingBox
from docyx.schema.models import Document, Element, Page, PageStatus

# A size must exceed body text by this factor before it counts as a heading.
# Below it, size variation is usually captions and footnotes, not hierarchy.
HEADING_RATIO = 1.15
MAX_HEADING_LEVEL = 6


def _body_size(elements: List[Element]) -> Optional[float]:
    """The most common font size, which is almost always body text."""
    sizes = [
        round(el.typography.font_size, 1)
        for el in elements
        if el.type == "text" and el.typography and el.typography.font_size
    ]
    if not sizes:
        return None
    return Counter(sizes).most_common(1)[0][0]


def _heading_levels(elements: List[Element], body: Optional[float]) -> Dict[float, int]:
    """Map each heading-sized font size to a Markdown level, largest = h1."""
    if body is None:
        return {}
    bigger = sorted(
        {
            round(el.typography.font_size, 1)
            for el in elements
            if el.type == "text"
            and el.typography
            and el.typography.font_size
            and el.typography.font_size >= body * HEADING_RATIO
        },
        reverse=True,
    )
    return {size: min(i + 1, MAX_HEADING_LEVEL) for i, size in enumerate(bigger)}


def _is_bold(el: Element) -> bool:
    # PyMuPDF span flags: bit 4 is bold.
    return bool(el.typography and el.typography.flags and el.typography.flags & (1 << 4))


def _table_markdown(table: Element) -> str:
    """Render a table element's cell children as a Markdown grid."""
    if not table.children:
        return "<!-- table detected, structure not recovered -->"

    grid: Dict[int, Dict[int, str]] = {}
    for cell in table.children:
        if not cell.grid:
            continue
        grid.setdefault(cell.grid.row, {})[cell.grid.column] = (cell.text or "").strip()

    if not grid:
        return "<!-- table detected, structure not recovered -->"

    width = max((max(cols) for cols in grid.values()), default=0) + 1
    rows = [[grid.get(r, {}).get(c, "") for c in range(width)] for r in sorted(grid)]

    # Markdown requires a header row; the first row stands in when no header
    # role was detected.
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    out += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(out)


# A line's x may exceed its paragraph's left edge by a pixel or two from glyph
# overhang; a real first-line indent is an order of magnitude larger (23px at
# 150 DPI in the arXiv corpus).
INDENT_TOLERANCE = 10.0


def _join_lines(parts: List[str]) -> str:
    """Join wrapped lines back into a paragraph, repairing line-break hyphens.

    PDF stores what was drawn, so a word broken across lines arrives as
    "arbi-" + "trary". Joining on a space yields "arbi- trary", which is a
    corrupted token to anything downstream — a retrieval index, a tokenizer, a
    training target.

    ponytail: a trailing hyphen followed by a lowercase letter is treated as a
    line break, so a genuine compound broken at the margin ("well-" / "known")
    loses its hyphen. Telling those apart needs a dictionary; the trade is worth
    it because line breaks vastly outnumber compounds at the margin. Upgrade
    path is a lexicon check if a corpus proves otherwise.
    """
    out = ""
    for part in parts:
        if not out:
            out = part
        elif out.endswith("-") and part[:1].islower():
            out = out[:-1] + part
        else:
            out += " " + part
    return out


def _starts_paragraph(el: Element, left_edge: Optional[float]) -> bool:
    """Is this line indented relative to the paragraph it would otherwise join?

    A first-line indent is the only signal in the geometry that a new paragraph
    began; without it every column collapses into one block of prose.

    ponytail: left-edge indents only, so right-to-left pages (where the indent
    is on the right) and paragraphs marked by vertical space alone are missed.
    """
    if left_edge is None:
        return False
    return el.geometry.bbox.x > left_edge + INDENT_TOLERANCE


# A line assembled from this many separate elements is structured, not prose:
# real prose arrives one element per line. Three is the smallest count that
# cannot happen by accident from a run-in heading or a trailing citation.
TABULAR_PARTS = 3


def _style_role(el: Element, levels: Dict[float, int]) -> Tuple[Optional[int], bool]:
    """What this element would render as: heading level, and boldness.

    Merging is restricted to neighbours sharing a role. Without that guard a
    bold heading absorbs the run-in sentence that starts on its baseline —
    "Input/Output Representations" + "To make BERT" became one heading.
    """
    size = (
        round(el.typography.font_size, 1)
        if el.typography and el.typography.font_size
        else None
    )
    return levels.get(size), _is_bold(el)


def _visual_lines(
    ordered: List[Element], levels: Dict[float, int]
) -> List[Tuple[Element, int]]:
    """Group elements sharing a baseline into one visual line.

    Extraction emits a line per text run, so anything set with wide internal
    gaps — a table row, a numbered heading — arrives as several elements on one
    baseline. Treated separately they each become their own block, which turns
    an 8-column table into 8 paragraphs per row and a heading into two headings.

    Returns each line with the number of elements it was assembled from, so the
    caller can tell a table row from a sentence.

    Safe against merging across columns: XY-cut emits a whole column before the
    next begins, so two elements adjacent in *reading order* and sharing a
    baseline are in the same column by construction.
    """
    lines: List[Tuple[Element, int]] = []
    for el in ordered:
        if lines:
            previous, parts = lines[-1]
            a, b = previous.geometry.bbox, el.geometry.bbox
            same_role = _style_role(previous, levels) == _style_role(el, levels)
            if same_role and min(a.y1, b.y1) - max(a.y, b.y) > 0:
                top, bottom = min(a.y, b.y), max(a.y1, b.y1)
                left, right = min(a.x, b.x), max(a.x1, b.x1)
                lines[-1] = (
                    previous.model_copy(
                        update={
                            "text": f"{(previous.text or '').strip()} {(el.text or '').strip()}",
                            "geometry": previous.geometry.model_copy(
                                update={
                                    "bbox": BoundingBox(
                                        x=left, y=top, width=right - left, height=bottom - top
                                    )
                                }
                            ),
                        }
                    ),
                    parts + 1,
                )
                continue
        lines.append((el, 1))
    return lines


def _page_markdown(page: Page) -> str:
    if page.status is PageStatus.FAILED:
        reasons = ", ".join(e.code for e in page.errors) or "unknown"
        return f"<!-- page {page.page_number}: no valid result ({reasons}) -->"

    ordered = sorted(
        [el for el in page.elements if el.reading_order is not None],
        key=lambda el: el.reading_order,
    )
    body = _body_size(page.elements)
    levels = _heading_levels(page.elements, body)

    lines: List[str] = []
    paragraph: List[str] = []
    left_edge: Optional[float] = None

    def flush():
        if paragraph:
            lines.append(_join_lines(paragraph))
            lines.append("")
            paragraph.clear()

    tabular = False
    for el, parts in _visual_lines(ordered, levels):
        text = (el.text or "").strip()
        if not text:
            continue
        size = round(el.typography.font_size, 1) if el.typography and el.typography.font_size else None
        level = levels.get(size)
        if level:
            flush()
            left_edge = None
            lines.append(f"{'#' * level} {text}")
            lines.append("")
        elif _is_bold(el) and len(text) < 80:
            flush()
            left_edge = None
            lines.append(f"**{text}**")
            lines.append("")
        elif parts >= TABULAR_PARTS:
            # Structured, so keep the row on its own line. Consecutive rows are
            # not separated by blank lines, or the table reads as a list of
            # unrelated fragments rather than a block.
            flush()
            left_edge = None
            if not tabular:
                tabular = True
            lines.append(text)
        else:
            if tabular:
                lines.append("")
                tabular = False
            if _starts_paragraph(el, left_edge):
                flush()
                # Re-seed from this line. Keeping the old edge would compare
                # every subsequent line against the *previous* column's margin,
                # so a second column — indented relative to the first by
                # definition — would split at every single line.
                left_edge = None
            paragraph.append(text)
            box = el.geometry.bbox
            left_edge = box.x if left_edge is None else min(left_edge, box.x)
    if tabular:
        lines.append("")
    flush()

    # Tables and figures carry no reading order (they are containers, see
    # ReadingOrderCalculator), so they are appended after the text flow.
    for el in page.elements:
        if el.type == "table":
            lines.append(_table_markdown(el))
            lines.append("")
        elif el.type in {"figure", "image"}:
            box = el.geometry.bbox
            lines.append(f"![figure](#page-{page.page_number}-at-{int(box.x)}-{int(box.y)})")
            lines.append("")

    if page.warnings:
        codes = ", ".join(w.code for w in page.warnings)
        lines.append(f"<!-- page {page.page_number} warnings: {codes} -->")
        lines.append("")

    return "\n".join(lines).rstrip()


def to_markdown(document: Document, page_separator: str = "\n\n---\n\n") -> str:
    """Render a whole document. Pages keep their order; failed pages become a
    comment rather than vanishing, so the output never silently loses a page."""
    return page_separator.join(_page_markdown(page) for page in document.pages).strip() + "\n"
