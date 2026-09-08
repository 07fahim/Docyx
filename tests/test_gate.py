from docyx.pipeline.gate import GateError, GateResult, TextLayerGate


def test_text_layer_gate_pass():
    gate = TextLayerGate()
    result = gate.check_page("sample_document.pdf", page_num=1)
    assert isinstance(result, GateResult)
    assert result.passed is True
    assert result.error is None


def test_text_layer_gate_fail():
    gate = TextLayerGate()
    result = gate.check_page("sample_fail_scanned.pdf", page_num=1)
    assert isinstance(result, GateResult)
    assert result.passed is False
    assert result.error is not None
    assert isinstance(result.error, GateError)
    assert result.error.code == "NO_TEXT_LAYER"
    assert result.error.stage == "text_layer_detection"
    assert result.error.message == "Page contains no extractable native text"
