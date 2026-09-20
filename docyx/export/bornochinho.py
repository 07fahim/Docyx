"""Export as BornoChinho annotation files, paired with page images.

BornoChinho is an annotation tool for building OCR training data from scanned
pages. Its manual states the job exactly: the category and the words are
usually already typed, and *"What is missing is the position, where on the
image that block actually is."*

Producing positions is what Docyx does, so this writes its output in the shape
that tool reads — turning a measuring pass into a checking pass.

    python -m docyx book.pdf -f bornochinho -o out/ --layout

Its schema is deliberately closed: `{category, text, bbox}` and *"Nothing else
is allowed in an entry"*. So this is a lossy export by design — confidence,
provenance, script, typography and reading order are all dropped. Use `json`
or `bundle` when any of that matters.

Files are written flat with matching stems, because that pairing is how the
tool associates an image with its annotation, and it only reads files sitting
directly inside the chosen folder:

    out/
    ├── book_page_0001.png
    ├── book_page_0001.json
    └── ...
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from docyx.core.metadata import ProvenanceSource
from docyx.pdf.renderer import PDFRenderer
from docyx.schema.models import Document, Element, Page

#: Docyx type -> the ten names BornoChinho accepts. Capitalisation matters to
#: it ("Text" works, "text" does not), so these are written out rather than
#: derived: a renamed type should fail loudly here, not silently export a
#: category its validator rejects.
CATEGORIES: Dict[str, str] = {
    "text": "Text",
    "text_region": "Text",
    "title": "Title",
    "section_header": "Section-header",
    "list_item": "List-item",
    "caption": "Caption",
    "footnote": "Footnote",
    "page_header": "Page-header",
    "page_footer": "Page-footer",
    "table": "Table",
    "picture": "Picture",
    "figure": "Picture",
}

#: The one category with no words. Giving it text is rejected as an
#: "Unexpected field", and withholding text from anything else is a
#: "Missing field".
PICTURE = "Picture"

#: DocLayNet has 11 classes and BornoChinho accepts 10. A formula is not
#: prose, so exporting it as Text would poison the very labels this file
#: exists to produce. Dropped and counted instead — a block with no box is
#: something their review pass looks for, a mislabelled one is not.
UNMAPPABLE = {"formula"}


def _bbox(element: Element, width: int, height: int) -> List[int]:
    """150 DPI top-left x/y/w/h -> [left, top, right, bottom], 1-indexed.

    Docyx's reference pixels are already this image's pixels, so there is no
    scaling — only the origin convention differs: *"The very top-left of the
    picture counts as 1, not 0."* Clamped, not shifted; shifting every
    coordinate by one would move every box.
    """
    box = element.geometry.bbox
    left = min(max(1, round(box.x)), width)
    top = min(max(1, round(box.y)), height)
    # At least one pixel wide and tall: their validator rejects a box that is
    # inside out or has no size, and a zero-height rule would produce one.
    right = min(max(left + 1, round(box.x + box.width)), width)
    bottom = min(max(top + 1, round(box.y + box.height)), height)
    return [left, top, right, bottom]


def _regions(page: Page) -> List[Element]:
    return [
        el for el in page.elements
        if el.provenance.source is ProvenanceSource.LAYOUT_MODEL
        and el.type in CATEGORIES
    ]


def _contains(region: Element, line: Element) -> bool:
    """Centre-in-box, the same rule `_assign_roles` uses to type a line.

    Sharing the rule is the point: if the two disagreed, a line could be
    labelled by one region and exported inside another.
    """
    box, inner = region.geometry.bbox, line.geometry.bbox
    cx, cy = inner.x + inner.width / 2, inner.y + inner.height / 2
    return (box.x <= cx <= box.x + box.width) and (box.y <= cy <= box.y + box.height)


def _blocks(page: Page) -> List[Tuple[Element, Optional[str]]]:
    """The page as blocks, with the text each one should carry.

    BornoChinho's blocks are paragraphs; Docyx extracts at line granularity
    (§5). So when a layout model has run, its regions ARE the blocks and the
    lines inside them supply the words. Without one there are no regions, and
    lines are the best available answer — more entries than a person would
    draw, but every one of them correct.
    """
    regions = _regions(page)
    lines = [
        el for el in page.elements
        if el.provenance.source is not ProvenanceSource.LAYOUT_MODEL
    ]
    if not regions:
        return [(el, el.text) for el in lines]

    # Smallest first so the innermost region claims a line, as roles do.
    regions.sort(key=lambda el: el.geometry.bbox.width * el.geometry.bbox.height)
    claimed = set()
    blocks = []
    for region in regions:
        inside = [
            el for el in lines
            if id(el) not in claimed and el.text and _contains(region, el)
        ]
        claimed.update(id(el) for el in inside)
        inside.sort(key=lambda el: (el.reading_order is None, el.reading_order))
        blocks.append((region, " ".join(el.text for el in inside) or None))

    # A line in no region is still a block on the page. Dropping it would
    # produce exactly the defect their review pass hunts for: a block with
    # no box around it.
    blocks.extend((el, el.text) for el in lines if id(el) not in claimed)
    blocks.sort(key=lambda b: (b[0].geometry.bbox.y, b[0].geometry.bbox.x))
    return blocks


def page_entries(page: Page) -> Tuple[List[Dict], List[str]]:
    """One page as BornoChinho entries, plus a reason for anything dropped."""
    entries: List[Dict] = []
    dropped: List[str] = []

    for element, text in _blocks(page):
        if element.type in UNMAPPABLE:
            dropped.append(f"{element.id}: {element.type} has no BornoChinho category")
            continue
        category = CATEGORIES.get(element.type)
        if category is None:
            dropped.append(f"{element.id}: {element.type} is not an exportable block")
            continue
        if category != PICTURE and not (text or "").strip():
            dropped.append(f"{element.id}: {category} with no text would fail their check")
            continue

        entry: Dict = {"category": category,
                       "bbox": _bbox(element, page.width, page.height)}
        if category != PICTURE:
            entry["text"] = text
        entries.append(entry)

    return entries, dropped


def validate(entries: List[Dict], width: int, height: int) -> List[str]:
    """Their §10 checks, run before writing rather than after.

    Claiming compatibility is cheap; this is what makes it testable. A file
    this refuses is a file their Save would refuse, and finding out here beats
    finding out in front of the page.
    """
    problems = []
    for index, entry in enumerate(entries, start=1):
        if entry.get("category") not in set(CATEGORIES.values()):
            problems.append(f"entry {index}: unknown category {entry.get('category')!r}")
        expected = {"category", "bbox"} | ({"text"} if entry.get("category") != PICTURE else set())
        if set(entry) != expected:
            problems.append(f"entry {index}: fields {sorted(entry)} != {sorted(expected)}")
        box = entry.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            problems.append(f"entry {index}: bbox must be four numbers")
            continue
        left, top, right, bottom = box
        if left >= right:
            problems.append(f"entry {index}: bounding box is inside out")
        if top >= bottom:
            problems.append(f"entry {index}: bounding box is upside down")
        if min(box) < 1:
            problems.append(f"entry {index}: bounding box off the page")
        if right > width or bottom > height:
            problems.append(f"entry {index}: bounding box past the image edge")
    return problems


def write_bornochinho(document: Document, pdf_path: str, target: Path) -> List[Path]:
    """Write one image and one annotation per page, stems matching."""
    target.mkdir(parents=True, exist_ok=True)
    stem = Path(pdf_path).stem
    written: List[Path] = []

    with PDFRenderer(pdf_path) as renderer:
        for page in document.pages:
            entries, dropped = page_entries(page)
            problems = validate(entries, page.width, page.height)
            if problems:
                raise ValueError(
                    f"page {page.page_number} would be rejected by BornoChinho:\n  "
                    + "\n  ".join(problems)
                )

            name = f"{stem}_page_{page.page_number:04d}"
            image = target / f"{name}.png"
            image.write_bytes(renderer.render_page(page.page_number - 1))
            annotation = target / f"{name}.json"
            annotation.write_text(
                json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            written.extend([image, annotation])
            if dropped:
                print(f"  page {page.page_number}: dropped {len(dropped)} block(s)")
                for reason in dropped:
                    print(f"    {reason}")

    return written
