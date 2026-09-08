"""The narrow surface the rest of the pipeline needs from a PDF backend.

PyMuPDF is contained to ``docyx/pdf/``. Stages outside it depend on these
protocols instead of importing ``fitz``, so swapping the backend — should the
AGPL position ever change (see LICENSING.md) — stays a change to one package
rather than a change everywhere.
"""

from typing import Protocol


class TextPage(Protocol):
    def get_text(self, kind: str) -> str: ...


class TextDocument(Protocol):
    def __getitem__(self, page_num: int) -> TextPage: ...
