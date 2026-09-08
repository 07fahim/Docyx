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
