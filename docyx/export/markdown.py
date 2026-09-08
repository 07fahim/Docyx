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
from typing import Dict, List, Optional

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

    def flush():
        if paragraph:
            lines.append(" ".join(paragraph))
            lines.append("")
            paragraph.clear()

    for el in ordered:
        text = (el.text or "").strip()
        if not text:
            continue
        size = round(el.typography.font_size, 1) if el.typography and el.typography.font_size else None
        level = levels.get(size)
        if level:
            flush()
            lines.append(f"{'#' * level} {text}")
            lines.append("")
        elif _is_bold(el) and len(text) < 80:
            flush()
            lines.append(f"**{text}**")
            lines.append("")
        else:
            paragraph.append(text)
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
