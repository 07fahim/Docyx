from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

from docyx.core.geometry import Geometry
from docyx.core.metadata import Confidence, Provenance
from docyx.schema.errors import GateError


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


class Element(BaseModel):
    id: str
    type: str
    geometry: Geometry
    confidence: Confidence
    provenance: Provenance
    text: Optional[str] = None
    typography: Optional[Typography] = None


class Page(BaseModel):
    page_number: int
    status: PageStatus
    width: int
    height: int
    source_type: str = "born_digital"
    # Structured, so consumers never have to re-parse a serialized error.
    errors: List[GateError] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    elements: List[Element] = Field(default_factory=list)
    diagnostic_elements: List[Element] = Field(default_factory=list)


class Document(BaseModel):
    schema_version: str = "1.1"
    document_id: str
    pages: List[Page] = Field(default_factory=list)
