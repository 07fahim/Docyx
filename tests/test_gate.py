import fitz
import pytest

from docyx.pipeline.gate import GateError, GateResult, TextLayerGate


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
    assert isinstance(result.error, GateError)
    assert result.error.code == "NO_TEXT_LAYER"
    assert result.error.stage == "text_layer_detection"
    assert result.error.message == "Page contains no extractable native text"


def test_whitespace_only_text_fails_the_gate():
    d = fitz.open()
    d.new_page().insert_text((50, 50), "   ")
    assert TextLayerGate().check_page(d, page_num=0).passed is False
    d.close()
