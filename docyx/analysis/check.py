"""Defects a person would look for on a rendered page, found mechanically.

The workspace exists as a checking surface: its whole argument is that a claim
should be read against the pixels it came from. That still leaves the reviewer
scanning a page for the handful of faults that are tedious to spot and trivial
to describe — a box with no size, two boxes stacked on the same block, an
outlined region holding no text.

These are not schema errors. A page carrying every one of them validates, and
export will happily write it; `PageIssue` covers what the *gate* found, which
is a different question. So this is a separate report, and it is advisory: it
names elements, never changes them.

Deliberately four checks and no more. Each one is a fault seen on a real page
in this corpus, and a check that never fires on real input is a check nobody
can tell is broken.
"""

from dataclasses import dataclass, field
from typing import List

from docyx.schema.models import Element, Page

#: Two top-level boxes overlapping by more than this share of the smaller one
#: are reporting the same block twice.
STACKED_RATIO = 0.8

#: How far past the page edge a box may sit before it is reported, as a share
#: of page width. Justified RTL lines legitimately start left of zero:
#: `wiki_ar.pdf` p6 has 31 of them and the worst is 11.9px, while every other
#: corpus page overshoots by exactly 0. At 2% of 1242px this allows ~25px --
#: about one line height at 150 DPI -- so the check fires on a box that has
#: left the page rather than on one that touches the margin.
OFF_PAGE_TOLERANCE = 0.02


@dataclass
class Finding:
    """One defect. `code` is stable; consumers branch on it, never on message."""

    code: str
    message: str
    ids: List[str] = field(default_factory=list)


def _area(element: Element) -> float:
    bbox = element.geometry.bbox
    return bbox.width * bbox.height


def _overlap(a: Element, b: Element) -> float:
    """Intersection over the smaller box, so a small box wholly inside a large
    one still reads as stacked — which is exactly the duplicate-region case."""
    p, q = a.geometry.bbox, b.geometry.bbox
    x = max(0.0, min(p.x + p.width, q.x + q.width) - max(p.x, q.x))
    y = max(0.0, min(p.y + p.height, q.y + q.height) - max(p.y, q.y))
    smaller = min(_area(a), _area(b))
    return (x * y) / smaller if smaller else 0.0


def check_page(page: Page) -> List[Finding]:
    """Advisory findings for one page, worst first."""
    findings: List[Finding] = []
    elements = page.elements

    degenerate = [
        el.id for el in elements
        if el.geometry.bbox.width <= 0 or el.geometry.bbox.height <= 0
    ]
    if degenerate:
        findings.append(Finding(
            "ZERO_SIZE_BOX",
            f"{len(degenerate)} elements have a box with no width or no height, "
            "so nothing is drawn where they claim to be.",
            degenerate,
        ))

    # The rendered image IS the page at 150 DPI, so a box past its edge points
    # at nothing. `export/blocks.py` clamps these on the way out; a reviewer
    # should see them before that happens.
    slack = page.width * OFF_PAGE_TOLERANCE
    outside = [
        el.id for el in elements
        if el.geometry.bbox.x < -slack or el.geometry.bbox.y < -slack
        or el.geometry.bbox.x + el.geometry.bbox.width > page.width + slack
        or el.geometry.bbox.y + el.geometry.bbox.height > page.height + slack
    ]
    if outside:
        findings.append(Finding(
            "BOX_OFF_PAGE",
            f"{len(outside)} elements sit more than {round(slack)}px past the "
            "page edge, so they point at pixels that do not exist.",
            outside,
        ))

    empty = [el.id for el in elements if el.type == "text" and not (el.text or "").strip()]
    if empty:
        findings.append(Finding(
            "EMPTY_TEXT",
            f"{len(empty)} text elements carry no text, so they export as a box "
            "with nothing in it.",
            empty,
        ))

    # Top-level only: a line inside its own layout region overlaps it totally
    # and by design, and reporting that would bury the real duplicates.
    sized = [el for el in elements if _area(el) > 0]
    stacked = sorted({
        el_id
        for i, a in enumerate(sized)
        for b in sized[i + 1:]
        if _overlap(a, b) >= STACKED_RATIO and a.type == b.type
        for el_id in (a.id, b.id)
    })
    if stacked:
        findings.append(Finding(
            "STACKED_BOXES",
            f"{len(stacked)} elements lie almost entirely inside another of the "
            "same type — either one block detected twice, or a fragment "
            "extracted as its own line.",
            stacked,
        ))

    return findings
