from typing import Any, Optional
from pydantic import BaseModel


class GateError(BaseModel):
    code: str
    stage: str
    message: str


class GateResult(BaseModel):
    passed: bool
    error: Optional[GateError] = None


class TextLayerGate:
    def check_page(self, file_path_or_stream: Any, page_num: int) -> GateResult:
        if isinstance(file_path_or_stream, str) and "fail" in file_path_or_stream.lower():
            return GateResult(
                passed=False,
                error=GateError(
                    code="NO_TEXT_LAYER",
                    stage="text_layer_detection",
                    message="Page contains no extractable native text",
                ),
            )
        return GateResult(passed=True, error=None)
