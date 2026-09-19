import unicodedata
from typing import Dict, Optional

from pydantic import BaseModel

from docyx.pdf.protocols import TextDocument
from docyx.schema.errors import PageIssue

# U+FFFD (mapping failed) and the BMP private use area, where subsetted fonts
# with no usable ToUnicode CMap dump their glyphs.
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

    Failing the gate marks the page unusable for text output but never stops
    rendering or visual detection (§18.1). Presence is not quality (§18.3): a
    page whose text is extractable but garbled passes with a warning.
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

        # Order matters: GateResult carries one warning, most severe first.
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
                        "words in reordering scripts (Bengali, Devanagari, Tamil, Telugu) "
                        "are scrambled. Characters may also be missing outright — this is a "
                        "symptom of a broken font mapping, not only of ordering, and the "
                        "text is not recoverable by rearranging it"
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
    """Whether RTL text is stored in visual rather than logical order.

    In logical order an opening delimiter precedes its closer; a visual-order
    stream emits them mirrored (']5[' for '[5]'). Any line CONTAINING RTL
    characters is checked, not only majority-RTL ones, because a bilingual
    line is the common case in these documents.
    """
    for line in text.splitlines():
        if not any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in line):
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
    """Whether the text is in glyph order rather than logical order.

    Indic scripts draw a vowel sign before the consonant it follows, so a
    producer writing glyph order emits the mark first, and a dependent vowel
    sign can never legitimately begin a word. Decided by Unicode category, so
    it is not specific to one script: verified on Bengali, Devanagari, Tamil
    and Telugu.

    Thai and Lao are out of scope BY DESIGN — Unicode stores their pre-base
    vowels before the consonant, so a leading vowel there is correct, and
    flagging it would fail every correct Thai page. See tests/test_gate.py.

    The warning understates the damage: characters may also be missing
    outright, so the text is not recoverable by reordering.
    """
    words = text.split()
    if not words:
        return False
    orphans = sum(1 for w in words if unicodedata.category(w[0]) in ("Mn", "Mc"))
    return orphans / len(words) >= ratio
