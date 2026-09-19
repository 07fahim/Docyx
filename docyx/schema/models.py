import unicodedata
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, computed_field

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


def script_of(text: str) -> Optional[str]:
    """Which writing system the text is in — derived, never guessed.

    §7 of the plan asks for `language`, and this is deliberately not that. A
    PDF carries no language field, so `"en"` could only come from statistical
    detection, and a guess cannot honestly sit in a schema where every value
    declares its own reliability. `direction` replaced it in v1.3 for exactly
    that reason.

    Script, unlike language, IS in the characters. Unicode names each codepoint
    after its script — U+0995 is BENGALI LETTER KA — so the first word of the
    name is the answer, with no table to maintain and no model to run. It
    cannot distinguish English from French, and does not pretend to; it CAN
    distinguish Bengali from Arabic from Latin, which is the distinction the
    documents this tool exists for actually turn on.

    Majority vote over letters only. Digits and punctuation are shared between
    scripts and would drag every line towards whichever script names them.
    """
    scripts: Dict[str, int] = {}
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if not name:
            continue
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

    # Derived from `flags` rather than stored alongside it, so the two can
    # never disagree. §7 asks for bold and italic by name; making a consumer
    # decode a PyMuPDF bitfield to get them is not "structured output".
    # None, not False, when there are no flags at all — "this PDF did not say"
    # is a different claim from "this text is not bold".

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
    """Where a line sits inside its paragraph — §7's alignment, line height and
    indentation.

    A separate object from `Typography`, deliberately. Typography is documented
    as "carried verbatim from the PDF text layer"; these three are **measured
    from geometry**, and mixing read values with computed ones inside an object
    whose whole claim is that it reports what the file said would quietly break
    the provenance story this schema is built on.

    All three are in 150-DPI reference pixels, like every other measurement.

    `alignment` follows one explicit rule, applied per line against its own
    block: flush on both edges is `justify`, flush left only is `left`, flush
    right only is `right`, equal gaps on both sides is `center`, anything else
    is None. A single-line block is None — one line cannot reveal the shape of
    a paragraph, and guessing "left" for every heading and caption on the page
    would be a confident lie.
    """

    #: Distance from the paragraph's leading edge. Measured from the RIGHT in
    #: RTL blocks, so a positive indent always means "pushed in from where this
    #: script starts", not "pushed in from the left of the page".
    indent: Optional[float] = None
    #: Top-to-top distance to the next line in the same block. None on the last
    #: line, where there is nothing to measure against.
    line_height: Optional[float] = None
    #: "left" | "right" | "center" | "justify", or None when undecidable.
    alignment: Optional[str] = None


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
    #: Where the line sits in its paragraph (§7), measured from geometry.
    layout: Optional[TextLayout] = None
    #: Writing system, derived from the characters (§7). NOT language — see
    #: `script_of`. None when the text holds no letters at all.
    script: Optional[str] = None
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
    """One PDF in, one Document out.

    `filename` and `page_count` are flat rather than nested under a `document`
    object as §4's example shows: nesting would move `document_id` and
    `schema_version` and break every existing consumer, which §19 reserves for
    a major version. The information is the same.

    `page_count` is the document's OWN length, which `len(pages)` is not once
    `--pages` has selected a subset — so a caller can always tell a 3-page
    document from three pages of a 300-page one.
    """

    schema_version: str = "1.6"
    document_id: str
    filename: Optional[str] = None
    page_count: Optional[int] = None
    pages: List[Page] = Field(default_factory=list)