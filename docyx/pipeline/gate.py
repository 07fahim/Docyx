import unicodedata
from typing import Dict, Optional

from pydantic import BaseModel

from docyx.pdf.protocols import TextDocument
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

    def check_page(self, doc: TextDocument, page_num: int) -> GateResult:
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

        # Order matters: a garbled text layer is the more severe finding, and
        # GateResult carries one warning. Both degrade the page to `partial`.
        ratio = _suspect_ratio(text)
        if ratio < self.suspect_ratio and _combining_mark_order(text):
            return GateResult(
                passed=True,
                warning=PageIssue(
                    code="COMBINING_MARK_ORDER",
                    stage="text_layer_quality",
                    message=(
                        "combining marks appear before the characters they attach to; "
                        "the text layer is in glyph order rather than logical order, so "
                        "words in reordering scripts (Bengali, Devanagari, Thai) are "
                        "scrambled even though every character is present"
                    ),
                ),
            )

        if ratio < self.suspect_ratio and _rtl_visual_order(text):
            return GateResult(
                passed=True,
                warning=PageIssue(
                    code="RTL_VISUAL_ORDER",
                    stage="text_layer_quality",
                    message=(
                        "right-to-left text appears to be stored in visual rather than "
                        "logical order; digits, brackets and Latin runs on RTL lines are "
                        "likely reversed and cannot be recovered from the text layer"
                    ),
                ),
            )

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


_CLOSERS = {")": "(", "]": "[", "}": "{"}


def _rtl_visual_order(text: str) -> bool:
    """Is right-to-left text stored in visual order rather than logical order?

    In logical order an opening delimiter always precedes its closer. A PDF
    that bakes the bidi reordering into the glyph stream emits the pair
    mirrored — ']5[' where the document reads '[5]' — which is unambiguous
    evidence that the bidi-neutral runs on that line are in visual order.

    Only majority-RTL lines are inspected, so unmatched punctuation in Latin
    prose ("see b) above") cannot trigger it.

    ponytail: mirrored delimiters only. A stray unmatched closer inside genuine
    RTL prose would false-positive; detecting reversed digit runs directly
    would need the bidi algorithm, which is the thing we are avoiding. Warning,
    not an error, for exactly that reason.
    """
    for line in text.splitlines():
        categories = [unicodedata.bidirectional(ch) for ch in line]
        rtl = sum(1 for c in categories if c in ("R", "AL"))
        if rtl <= sum(1 for c in categories if c == "L"):
            continue

        open_depth: Dict[str, int] = {}
        for ch in line:
            if ch in _CLOSERS.values():
                open_depth[ch] = open_depth.get(ch, 0) + 1
            elif ch in _CLOSERS and open_depth.get(_CLOSERS[ch], 0) == 0:
                return True
            elif ch in _CLOSERS:
                open_depth[_CLOSERS[ch]] -= 1
    return False


def _combining_mark_order(text: str, ratio: float = 0.02) -> bool:
    """Is the text in glyph order rather than logical order?

    Indic and South-East Asian scripts reorder on display: in বাংলা the vowel
    sign is typed after its consonant and drawn before it. A producer that
    writes glyph order therefore emits the mark first — and a dependent vowel
    sign can never legitimately begin a word, which makes this cheap to spot.

    Measured: Word-produced Bengali scores 8.3%, the same language from Chrome
    scores 0%, and Arabic and Latin score 0%. So the defect is a property of
    the producer, not the script.

    Distinct from TEXT_LAYER_SUSPECT, which counts undecodable codepoints. Here
    every character decodes and the multiset is intact; only the order is wrong,
    so no amount of replacement-character counting would ever see it.

    ponytail: word-initial marks only. Marks misplaced *within* a cluster are
    invisible to this, and detecting those needs real grapheme segmentation.
    """
    words = text.split()
    if not words:
        return False
    orphans = sum(1 for w in words if unicodedata.category(w[0]) in ("Mn", "Mc"))
    return orphans / len(words) >= ratio
