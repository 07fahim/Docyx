from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

from docyx.core.geometry import Geometry
from docyx.core.metadata import Confidence, Provenance
from docyx.schema.errors import PageIssue


class PageStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class Typography(BaseModel):
    """Span-level type information, carried verbatim from the PDF text layer.

    ``font_size`` is in points (the unit the PDF authors in), not the 150 DPI
    reference pixels used for geometry.

    ``flags`` is the PyMuPDF span bitfield:
    bit 0 superscript, 1 italic, 2 serif, 3 monospace, 4 bold.
    ``color`` is packed sRGB (``0xRRGGBB``).
    """

    font_family: Optional[str] = None
    font_size: Optional[float] = None
    flags: Optional[int] = None
    color: Optional[int] = None


class GridPosition(BaseModel):
    """Where a cell sits in its table. Zero-indexed from the top-left."""

    row: int
    column: int
    row_span: int = 1
    column_span: int = 1


class Element(BaseModel):
    id: str
    type: str
    geometry: Geometry
    confidence: Confidence
    provenance: Provenance
    text: Optional[str] = None
    reading_order: Optional[int] = None
    typography: Optional[Typography] = None
    # Set on `table_cell` elements only; None everywhere else.
    grid: Optional[GridPosition] = None
    # Containment. A `table` holds its `table_cell` children here.
    children: List["Element"] = Field(default_factory=list)


Element.model_rebuild()


class Page(BaseModel):
    page_number: int
    status: PageStatus
    width: int
    height: int
    source_type: str = "born_digital"
    errors: List[PageIssue] = Field(default_factory=list)
    warnings: List[PageIssue] = Field(default_factory=list)
    elements: List[Element] = Field(default_factory=list)
    diagnostic_elements: List[Element] = Field(default_factory=list)


class Document(BaseModel):
    schema_version: str = "1.2"
    document_id: str
    pages: List[Page] = Field(default_factory=list)