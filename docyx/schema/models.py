import unicodedata
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

from docyx.core.constants import CANONICAL_DPI
from docyx.core.geometry import Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance
from docyx.schema.errors import PageIssue


class Direction(str, Enum):
    """Writing direction of a text element (§13).

    Tracked per element, not per page: a page may mix directions, and forcing
    one global direction on it loses that. Read from the line's direction vector
    (vertical vs horizontal) and the Unicode bidi category of the characters
    themselves (LTR vs RTL), so it is derived, never guessed.

    Not from the span's ``bidi`` embedding level, which real Arabic PDFs report
    as 0 on every span — see ``_direction`` in docyx/pdf/text_extractor.py.

    Per-element *language* is deliberately absent: PDFs carry no such field, so
    populating it would mean detection, which could not honestly be `exact`.
    """

    LTR = "ltr"
    RTL = "rtl"
    TTB = "ttb"
    UNKNOWN = "unknown"

    @classmethod
    def of_text(cls, text: str) -> "Direction":
        """Horizontal direction from the characters themselves.

        Lives here rather than in the extractor because OCR needs the same
        answer from the same evidence, and ``docyx/pdf/`` is PyMuPDF-contained
        (LICENSING.md) — an analyzer importing it would drag ``fitz`` outside
        the boundary.

        Never returns LTR by default: a line of digits and brackets is
        bidi-neutral, so it is genuinely UNKNOWN.
        """
        categories = [unicodedata.bidirectional(ch) for ch in text]
        rtl = sum(1 for c in categories if c in ("R", "AL"))
        ltr = sum(1 for c in categories if c == "L")
        if not rtl and not ltr:
            return cls.UNKNOWN
        return cls.RTL if rtl > ltr else cls.LTR


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
    #: Header cell rather than body (§11). False when the detector could not
    #: tell, which is not the same as "known to be body" — but distinguishing
    #: those would need a third state nothing currently produces.
    is_header: bool = False


class Element(BaseModel):
    id: str
    type: str
    geometry: Geometry
    confidence: Confidence
    provenance: Provenance
    text: Optional[str] = None
    reading_order: Optional[int] = None
    typography: Optional[Typography] = None
    direction: Optional[Direction] = None
    # Set on `table_cell` elements only; None everywhere else.
    grid: Optional[GridPosition] = None
    # Containment. A `table` holds its `table_cell` children here.
    children: List["Element"] = Field(default_factory=list)

    def edit_text(self, text: str) -> "Element":
        """Record a human correction to this element's text (§11).

        Kept here rather than left to each caller so an edit is applied one
        way: the machine's value is preserved on first edit only, so editing
        twice does not overwrite the original with the first correction.

        Confidence becomes `exact`. A person reading the rendered page is a
        better authority than any extractor — and on a page whose text layer
        is damaged or absent, they are the only one.
        """
        if not self.provenance.modified_by_user:
            self.provenance.original_text = self.text
        self.provenance.modified_by_user = True
        self.text = text
        self.confidence = Confidence(value=1.0, type=ConfidenceType.EXACT)
        return self


Element.model_rebuild()


class CoordinateSystem(BaseModel):
    """What every coordinate on this page means (§4, §16).

    Emitted rather than assumed. The invariant — top-left origin, reference
    pixels at 150 DPI — is enforced at every boundary in code and stated in
    CLAUDE.md, but it was never in the output, so a consumer reading the JSON
    had to already know it or guess from the numbers. For a format whose whole
    claim is that it describes itself, leaving the unit implicit was the one
    place it did not.

    Constant in v1. It is a field rather than a documented convention so that
    rendering at another DPI, or a backend with a bottom-left origin, is a
    value change instead of a silent reinterpretation of every box ever
    exported.
    """

    origin: str = "top_left"
    units: str = "reference_pixels"
    reference_resolution: int = CANONICAL_DPI


class Page(BaseModel):
    page_number: int
    status: PageStatus
    width: int
    height: int
    coordinate_system: CoordinateSystem = Field(default_factory=CoordinateSystem)
    source_type: str = "born_digital"
    errors: List[PageIssue] = Field(default_factory=list)
    warnings: List[PageIssue] = Field(default_factory=list)
    elements: List[Element] = Field(default_factory=list)
    diagnostic_elements: List[Element] = Field(default_factory=list)


class Document(BaseModel):
    schema_version: str = "1.5"
    document_id: str
    pages: List[Page] = Field(default_factory=list)