from typing import Optional

import fitz
from pydantic import BaseModel

from docyx.schema.errors import GateError


class GateResult(BaseModel):
    passed: bool
    error: Optional[GateError] = None


class TextLayerGate:
    """Decides whether a page carries a machine-readable text layer.

    Failing the gate marks the page as unusable for text output, but never
    prevents rendering or visual detection from running.
    """

    def check_page(self, doc: fitz.Document, page_num: int) -> GateResult:
        if doc[page_num].get_text("text").strip():
            return GateResult(passed=True, error=None)
        return GateResult(
            passed=False,
            error=GateError(
                code="NO_TEXT_LAYER",
                stage="text_layer_detection",
                message="Page contains no extractable native text",
            ),
        )
