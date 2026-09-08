from typing import Optional

import fitz
from pydantic import BaseModel

from docyx.schema.errors import PageIssue

# Codepoints that indicate the text layer decoded badly rather than the document
# genuinely containing these characters:
#   U+FFFD          the replacement char — a mapping failed outright
#   U+E000..U+F8FF  the BMP private use area — where subsetted fonts with no
#                   usable ToUnicode CMap dump their glyphs
REPLACEMENT_CHAR = "�"
PRIVATE_USE_START = ""
PRIVATE_USE_END = ""


def _suspect_ratio(text: str) -> float:
    """Fraction of characters that look like a decoding failure."""
    considered = [ch for ch in text if not ch.isspace()]
    if not considered:
        return 0.0
    bad = sum(
        1
        for ch in considered
        if ch == REPLACEMENT_CHAR or PRIVATE_USE_START <= ch <= PRIVATE_USE_END
    )
    return bad / len(considered)


class GateResult(BaseModel):
    passed: bool
    error: Optional[PageIssue] = None
    warning: Optional[PageIssue] = None


class TextLayerGate:
    """Decides whether a page carries a usable machine-readable text layer.

    Failing the gate marks the page as unusable for text output, but never
    prevents rendering or visual detection from running (§18.1).

    Presence is not quality (§18.3). A PDF exported by an older tool with
    subsetted fonts and no ToUnicode mapping returns text that is technically
    extractable but garbled — passing that through silently is worse than
    failing, because the caller has no way to tell. Such a page still passes
    (the text may be partly usable) but carries a TEXT_LAYER_SUSPECT warning,
    which degrades it to `partial`.

    ponytail: codepoint heuristics only, so this needs no new dependency.
    Reading each font's ToUnicode CMap via pikepdf would be more direct;
    `suspect_ratio` is the tuning knob until there is evidence it is needed.
    """

    def __init__(self, suspect_ratio: float = 0.10):
        self.suspect_ratio = suspect_ratio

    def check_page(self, doc: fitz.Document, page_num: int) -> GateResult:
        text = doc[page_num].get_text("text")
        if not text.strip():
            return GateResult(
                passed=False,
                error=PageIssue(
                    code="NO_TEXT_LAYER",
                    stage="text_layer_detection",
                    message="Page contains no extractable native text",
                ),
            )

        ratio = _suspect_ratio(text)
        if ratio >= self.suspect_ratio:
            return GateResult(
                passed=True,
                warning=PageIssue(
                    code="TEXT_LAYER_SUSPECT",
                    stage="text_layer_quality",
                    message=(
                        f"{ratio:.0%} of characters failed to decode to meaningful text; "
                        "the page has a text layer but it is likely garbled"
                    ),
                ),
            )
        return GateResult(passed=True)
