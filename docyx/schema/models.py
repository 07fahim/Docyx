"""The published output schema. See schema/v1.8.json for the generated form."""

import unicodedata
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, computed_field

from docyx.core.constants import CANONICAL_DPI
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance
from docyx.schema.errors import PageIssue


class Direction(str, Enum):
    """Writing direction of a text element (§13)."""

    LTR = "ltr"
    RTL = "rtl"
    TTB = "ttb"
    UNKNOWN = "unknown"

    @classmethod
    def of_text(cls, text: str) -> "Direction":
        """Horizontal direction from the Unicode bidi category of the characters.

        Not from the span's `bidi` level, which real Arabic PDFs report as 0.
        """
        categories = [unicodedata.bidirectional(ch) for ch in text]
        rtl = sum(1 for c in categories if c in ("R", "AL"))
        ltr = sum(1 for c in categories if c == "L")
        # Digits and brackets are bidi-neutral: unknown, not left-to-right.
        if not rtl and not ltr:
            return cls.UNKNOWN
        return cls.RTL if rtl > ltr else cls.LTR


def script_of(text: str) -> Optional[str]:
    """Writing system from the Unicode name of each letter, e.g. "bengali".

    Script, not language: a PDF records no language, so that would be a guess.
    """
    scripts: Dict[str, int] = {}
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if name:
            script = name.split()[0].lower()
            scripts[script] = scripts.get(script, 0) + 1
    if not scripts:
        return None
    return max(scripts, key=lambda s: scripts[s])


class PageStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


class Typography(BaseModel):
    """Type information carried verbatim from the PDF text layer.

    `font_size` is in points, not the 150 DPI pixels used for geometry.
    `flags` is the PyMuPDF span bitfield; `color` is packed sRGB.
    """

    font_family: Optional[str] = None
    font_size: Optional[float] = None
    flags: Optional[int] = None
    color: Optional[int] = None

    # Derived from `flags` so the two cannot disagree. None, not False, when
    # the PDF said nothing: "not stated" differs from "not bold".

    @computed_field  # type: ignore[prop-decorator]
    @property
    def bold(self) -> Optional[bool]:
        return None if self.flags is None else bool(self.flags & 16)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def italic(self) -> Optional[bool]:
        return None if self.flags is None else bool(self.flags & 2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def monospace(self) -> Optional[bool]:
        return None if self.flags is None else bool(self.flags & 8)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def serif(self) -> Optional[bool]:
        return None if self.flags is None else bool(self.flags & 4)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def superscript(self) -> Optional[bool]:
        return None if self.flags is None else bool(self.flags & 1)


class TextLayout(BaseModel):
    """Where a line sits inside its paragraph (§7), measured from geometry.

    Separate from Typography, which reports what the file said rather than
    what was computed. All values are 150 DPI reference pixels.
    """

    #: From the leading edge, so an RTL paragraph measures from the right.
    indent: Optional[float] = None
    #: Top-to-top distance to the next line. None on the last line.
    line_height: Optional[float] = None
    #: "left" | "right" | "center" | "justify". None when undecidable.
    alignment: Optional[str] = None


class GridPosition(BaseModel):
    """Where a cell sits in its table. Zero-indexed from the top-left."""

    row: int
    column: int
    row_span: int = 1
    column_span: int = 1
    #: False also means "the detector could not tell".
    is_header: bool = False


#: What a human may set `type` to (§8). Written out rather than imported from
#: `reading_order`, which imports this module; `test_assignable_types_cover_
#: the_orderable_roles` fails if the two drift. Containers are deliberately
#: absent: `text_region` and `table_cell` describe structure a person is not
#: reassigning one block at a time.
ASSIGNABLE_TYPES = frozenset({
    "text", "title", "section_header", "caption", "footnote",
    "formula", "list_item", "page_header", "page_footer", "table", "figure",
})


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
    layout: Optional[TextLayout] = None
    script: Optional[str] = None
    #: Set on `table_cell` elements only.
    grid: Optional[GridPosition] = None
    #: A `table` holds `table_cell` children; a mixed line holds `text_span`.
    children: List["Element"] = Field(default_factory=list)

    def edit_text(self, text: str) -> "Element":
        """Apply a human correction, preserving the machine's original (§11)."""
        # First edit only: otherwise the second save destroys the original.
        if self.provenance.original_text is None:
            # `or ""` so an element that had no text still records that it had
            # none: left None, the SECOND edit would claim the first correction
            # as the machine's original.
            self.provenance.original_text = self.text or ""
        self.provenance.modified_by_user = True
        self.text = text
        self.confidence = Confidence(value=1.0, type=ConfidenceType.EXACT)
        return self

    def edit_type(self, type_: str) -> "Element":
        """Correct the block's category (§8), preserving the machine's own.

        The third editable claim, and the one with no automatic answer:
        without a layout model every line is `text`, so a paper's title, its
        section headers and its footnotes are indistinguishable. A person
        reading the page is the only source of that distinction.
        """
        if type_ not in ASSIGNABLE_TYPES:
            raise ValueError(f"{type_!r} is not an assignable type")
        if self.provenance.original_type is None:
            self.provenance.original_type = self.type
        self.provenance.modified_by_user = True
        self.type = type_
        self.confidence = Confidence(value=1.0, type=ConfidenceType.EXACT)
        return self

    def edit_geometry(self, bbox: BoundingBox) -> "Element":
        """Move or resize an element, preserving the machine's original (§10).

        Same contract as `edit_text`, and separately guarded: correcting the
        text of a box whose position was already fixed must not overwrite the
        geometry the machine proposed, and vice versa.
        """
        if self.provenance.original_geometry is None:
            self.provenance.original_geometry = self.geometry.model_dump()
        self.provenance.modified_by_user = True
        self.geometry = self.geometry.model_copy(update={"bbox": bbox})
        self.confidence = Confidence(value=1.0, type=ConfidenceType.EXACT)
        return self


Element.model_rebuild()


class CoordinateSystem(BaseModel):
    """The units every coordinate on the page is expressed in (§4, §16)."""

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
    schema_version: str = "1.8"
    document_id: str
    filename: Optional[str] = None
    #: The document's own length, which differs from len(pages) when a subset
    #: of pages was requested.
    page_count: Optional[int] = None
    pages: List[Page] = Field(default_factory=list)
