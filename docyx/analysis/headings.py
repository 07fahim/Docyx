"""Propose heading types from font size, so `type` is not flat by default.

Without a layout model every extracted line is `text`, which makes a paper's
title, its section headers and its paragraphs indistinguishable — and makes
`type` useless to any consumer that cares what a block *is*.

The signal was already there and already computed: [export/markdown.py] ranks
font sizes to render `#`, then throws the ranking away. This puts it on the
element instead, as a `detected` claim a person can correct, which is the same
loop the rest of the pipeline runs on.

Deliberately not a model. It needs no weights, no download and no GPU, so it
runs on every page by default where `LayoutAnalyzer` does not.

**It only fires on prose.** Measured across the corpus, the share of lines at
the modal font size separates cleanly:

    wiki_ar p6   91%   2 proposals, both correct
    wiki_bn p5   89%   1, correct
    nasa p88     89%   2, both correct
    rfc2616 p12  96%   2, both correct
    arxiv p0     46%   6 proposals, one wrong
    irs_fw9 p0   44%   15 of 133 lines, mostly wrong
    irs_f1040 p0 71%   7, unreliable

A form has no dominant body size, so "bigger than body" stops meaning
"heading" and starts meaning "any of the other six sizes on this page".
Below `MIN_BODY_SHARE` this proposes nothing, on the same principle the
alignment code follows: an absent value beats a wrong one.

**It proposes; it never writes.** Mutating `type` during extraction would
force a lie about confidence: downgrading the element to `detected` claims the
*text* is uncertain when it was read exactly, and leaving it `exact` shows a
guessed category in the trust colour. `Confidence` describes the element, and
an element now carries three claims — text, geometry, type — that can differ
in how sure they are. Until those are separated, a suggestion a person accepts
is the honest shape: `edit_type` then records a human as the authority, which
is true.

    from docyx.analysis.headings import suggest
    for element_id, proposed in suggest(page.elements):
        ...
"""

import re
from collections import Counter
from typing import List, Optional, Tuple

from docyx.schema.models import Element

#: Element ids are positional and name their own page: `page89_b1_l0`.
PAGE_ID = re.compile(r"^page(\d+)_")

#: A size must exceed body by this much to read as a heading.
HEADING_RATIO = 1.15

#: Share of lines that must sit at the modal size before it counts as "body".
#: 0.8 splits the corpus above with nothing near the boundary.
MIN_BODY_SHARE = 0.8

#: Enough lines for a mode to mean anything.
MIN_LINES = 8

#: Largest size becomes `title`, everything else above body a `section_header`.
#: DocLayNet has no deeper heading class, so neither does this.
TITLE, SECTION = "title", "section_header"

def _first_page(element: Element) -> bool:
    """Page 1, read off the element's own id. Unparseable ids are treated as
    page 1, so an element built by hand in a test behaves as it reads."""
    match = PAGE_ID.match(element.id or "")
    return match is None or match.group(1) == "1"


def _sizes(elements: List[Element]) -> List[float]:
    return [
        round(el.typography.font_size, 1)
        for el in elements
        if el.type == "text" and el.typography and el.typography.font_size
    ]


def body_size(elements: List[Element]) -> Optional[float]:
    """The modal font size, or None when the page is not prose enough to have one."""
    sizes = _sizes(elements)
    if len(sizes) < MIN_LINES:
        return None
    size, count = Counter(sizes).most_common(1)[0]
    return size if count / len(sizes) >= MIN_BODY_SHARE else None


def suggest(elements: List[Element]) -> List[Tuple[str, str]]:
    """`(element_id, proposed_type)` for every line that reads as a heading.

    Only plain, untouched lines are considered: a layout model's answer and a
    person's correction both outrank a font-size guess, so proposing over
    either would be noise at best.
    """
    body = body_size(elements)
    if body is None:
        return []

    candidates = [
        el for el in elements
        if el.type == "text"
        and not el.provenance.modified_by_user
        and el.typography and el.typography.font_size
        and el.typography.font_size >= body * HEADING_RATIO
    ]
    if not candidates:
        return []

    sizes = [round(el.typography.font_size, 1) for el in candidates]
    largest = max(sizes)
    # A page has at most one title. Two lines sharing the largest size are two
    # section headings, not two titles — which is what `wiki_ar.pdf` p6 is.
    top = TITLE if sizes.count(largest) == 1 else SECTION
    # And a document has at most one title, on its first page. The largest line
    # on page 88 of a budget is a section heading however big it is; proposing
    # `title` there was wrong on every interior page in the corpus.
    if top == TITLE and not _first_page(candidates[0]):
        top = SECTION
    return [
        (el.id, top if size == largest else SECTION)
        for el, size in zip(candidates, sizes)
    ]
