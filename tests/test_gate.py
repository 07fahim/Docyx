import fitz
import pytest

from docyx.pipeline.gate import GateResult, TextLayerGate, _suspect_ratio
from docyx.schema.errors import PageIssue

PUA = chr(0xE000)  # private use area — where broken font subsets dump glyphs
REPLACEMENT = chr(0xFFFD)


@pytest.fixture
def doc():
    d = fitz.open()
    d.new_page().insert_text((50, 50), "Machine-readable text")
    d.new_page()  # blank page: no text layer
    yield d
    d.close()


def test_text_layer_gate_pass(doc):
    result = TextLayerGate().check_page(doc, page_num=0)
    assert isinstance(result, GateResult)
    assert result.passed is True
    assert result.error is None


def test_text_layer_gate_fail(doc):
    result = TextLayerGate().check_page(doc, page_num=1)
    assert isinstance(result, GateResult)
    assert result.passed is False
    assert isinstance(result.error, PageIssue)
    assert result.error.code == "NO_TEXT_LAYER"
    assert result.error.stage == "text_layer_detection"
    assert result.error.message == "Page contains no extractable native text"


def test_whitespace_only_text_fails_the_gate():
    d = fitz.open()
    d.new_page().insert_text((50, 50), "   ")
    assert TextLayerGate().check_page(d, page_num=0).passed is False
    d.close()


def test_clean_text_produces_no_warning():
    d = fitz.open()
    d.new_page().insert_text((50, 50), "Perfectly ordinary sentence.")
    result = TextLayerGate().check_page(d, 0)
    assert result.passed is True
    assert result.warning is None
    d.close()


class _FakeDoc:
    """A document whose text layer decoded badly.

    PyMuPDF's *writer* substitutes unmappable codepoints (to U+00B7) on insert,
    so a genuinely garbled text layer cannot be synthesized in-process. Its
    *reader* is what surfaces private-use and replacement characters on real
    files, and that is the boundary faked here.
    """

    def __init__(self, text: str):
        self._text = text

    def __getitem__(self, _page_num):
        return self

    def get_text(self, _kind):
        return self._text


def test_private_use_glyphs_flag_a_suspect_text_layer():
    """Subsetted fonts with no usable ToUnicode dump glyphs into the private
    use area — extractable, but meaningless (§18.3)."""
    result = TextLayerGate().check_page(_FakeDoc(PUA * 4), 0)

    # Still passes: presence is real even though quality is not, so whatever
    # text there is still reaches the caller.
    assert result.passed is True
    assert result.error is None
    assert result.warning is not None
    assert result.warning.code == "TEXT_LAYER_SUSPECT"
    assert result.warning.stage == "text_layer_quality"


def test_replacement_chars_flag_a_suspect_text_layer():
    result = TextLayerGate().check_page(_FakeDoc(REPLACEMENT * 4), 0)
    assert result.warning.code == "TEXT_LAYER_SUSPECT"


def test_a_few_bad_glyphs_do_not_trip_the_threshold():
    text = "One stray glyph " + PUA + " in an otherwise perfectly fine line of text."
    assert TextLayerGate().check_page(_FakeDoc(text), 0).warning is None


def test_suspect_ratio_is_tunable():
    text = "One stray glyph " + PUA + " in an otherwise perfectly fine line of text."
    assert TextLayerGate(suspect_ratio=0.01).check_page(_FakeDoc(text), 0).warning is not None


def test_whitespace_is_not_counted_toward_the_ratio():
    assert _suspect_ratio(PUA + " \t\n") == 1.0
    assert _suspect_ratio("   \n\t ") == 0.0
    assert _suspect_ratio("") == 0.0
    assert _suspect_ratio("clean text") == 0.0
    assert _suspect_ratio("ab" + PUA * 2) == 0.5


def test_rtl_text_in_visual_order_is_flagged(doc):
    """Measured on .corpus/wiki_ar.pdf, where PyMuPDF returns ']5[)2021(' for
    what the document reads as '(2021)[5]'.

    The Arabic words themselves come back in correct logical order; the
    bidi-neutral runs — digits, brackets, parentheses — do not, because the
    generator baked the bidi reordering into the glyph stream. Recovering
    logical order needs the bidi algorithm run backwards, which is ambiguous
    and lossy, so the honest response is to say so rather than return the text
    as `exact` with confidence 1.0.
    """
    from docyx.pipeline.gate import _rtl_visual_order

    assert _rtl_visual_order("متوسط العمر]1[)2020( سنة") is True
    assert _rtl_visual_order("متوسط العمر [1] (2020) سنة") is False


def test_latin_text_with_unmatched_brackets_is_not_flagged():
    """The check only inspects majority-RTL lines. A stray closer in English
    prose — 'see b) above' — must not degrade an otherwise clean page."""
    from docyx.pipeline.gate import _rtl_visual_order

    assert _rtl_visual_order("see b) above, and item 3] in the list") is False


def test_reordered_combining_marks_are_flagged():
    """Indic scripts reorder: in বাংলা the vowel sign is typed after its
    consonant but drawn before it. A producer that stores glyph order rather
    than logical order therefore emits the mark first, and a dependent vowel
    sign cannot legitimately begin a word.

    Measured on a Word-produced Bengali PDF (.corpus/word_bn.pdf): 8.3% of
    words start with a combining mark, against 0% for the same language from
    Chrome (.corpus/wiki_bn.pdf). The character multiset survives — only one
    nukta is lost — so this is an ordering defect, not a decoding one, and
    TEXT_LAYER_SUSPECT's replacement-character heuristic cannot see it.
    """
    from docyx.pipeline.gate import _combining_mark_order

    # Vowel sign U+09BF placed before its consonant, as glyph order would.
    assert _combining_mark_order("িক াঘ িচ") is True
    assert _combining_mark_order("কি ঘা চি বাংলাদেশ ঢাকা") is False


def test_latin_and_arabic_do_not_trigger_the_mark_check():
    from docyx.pipeline.gate import _combining_mark_order

    assert _combining_mark_order("ordinary English prose here") is False
    assert _combining_mark_order("تُعد مصر من أقدم الحضارات") is False


def test_rtl_inside_a_mostly_latin_line_is_still_flagged():
    """A bilingual line is the common case in the documents this targets, and
    the majority-RTL test missed it.

    'Mixed English و عربي together' is majority Latin, so the line was skipped
    and its Arabic run came back reversed and unflagged. Requiring only the
    PRESENCE of right-to-left characters catches it, and cannot fire on Latin
    prose because Latin prose contains none — verified zero false positives
    across every non-RTL document in the corpus.
    """
    from docyx.pipeline.gate import _rtl_visual_order

    assert _rtl_visual_order("See note ]12[ in العربية for details") is True
    assert _rtl_visual_order("See note [12] in العربية for details") is False
    assert _rtl_visual_order("see b) above and item 3] here") is False


# --- script coverage -------------------------------------------------------
#
# Added because "you only check Bengali" was a fair charge. The logic keys on
# Unicode categories rather than a script list, so it should generalise — these
# tests assert that it actually does, and record the one script it must NOT
# fire on.


@pytest.mark.parametrize(
    "script,scrambled",
    [
        ("Bengali", "োব ংলােদশ িদক্ষণ"),
        ("Devanagari", "िहन्दी ीदिल्ली"),
        ("Tamil", "ெசன்னை ெபரிய"),
        ("Telugu", "ెతలుగు ొకత్త"),
    ],
)
def test_glyph_order_is_detected_beyond_bengali(script, scrambled):
    """Every Indic script that places a vowel sign before its consonant on
    screen can be emitted in that order by a bad producer."""
    from docyx.pipeline.gate import _combining_mark_order

    assert _combining_mark_order(scrambled) is True, f"{script} went undetected"


def test_thai_leading_vowels_must_never_be_flagged():
    """Thai is the trap. Its pre-base vowels are category Lo, not Mn/Mc, AND
    Unicode stores them before the consonant by design — so a Thai word
    beginning with one is correct, not scrambled.

    An earlier docstring claimed Thai was covered. "Fixing" that by adding
    U+0E40..U+0E44 to the orphan test would have flagged every correct Thai
    page in existence.
    """
    from docyx.pipeline.gate import _combining_mark_order

    assert _combining_mark_order("เรียน แบบ โลก ใหม่ ไทย") is False


@pytest.mark.parametrize(
    "script,text",
    [
        ("Hebrew", "שלום ]5[ )2021( עולם"),
        ("Urdu", "اردو ]5[ )2021( زبان"),
        ("Persian", "فارسی ]5[ زبان"),
    ],
)
def test_visual_order_is_detected_beyond_arabic(script, text):
    """The check keys on the Unicode bidi category, so every right-to-left
    script is covered, not only the one it was written against."""
    from docyx.pipeline.gate import _rtl_visual_order

    assert _rtl_visual_order(text) is True, f"{script} went undetected"


@pytest.mark.parametrize(
    "script,text,expected",
    [
        ("Hindi", "हिन्दी भारत की राजभाषा है", "ltr"),
        ("Thai", "ภาษาไทยเป็นภาษาราชการ", "ltr"),
        ("Hebrew", "שלום עולם זהו טקסט", "rtl"),
        ("Urdu", "اردو پاکستان کی قومی زبان ہے", "rtl"),
        ("Chinese", "中文是世界上使用人数最多的语言", "ltr"),
        ("Japanese", "日本語のテキストです", "ltr"),
        ("Korean", "한국어 텍스트입니다", "ltr"),
        ("Russian", "Это русский текст", "ltr"),
        ("Greek", "Αυτό είναι ελληνικό κείμενο", "ltr"),
    ],
)
def test_direction_covers_every_script_not_just_the_measured_ones(script, text, expected):
    from docyx.schema.models import Direction

    assert Direction.of_text(text).value == expected, f"{script} misclassified"
