from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple, Union

from docyx.analysis.layout import LayoutAnalyzer
from docyx.analysis.ocr import OCRAnalyzer
from docyx.analysis.reading_order import TEXT_ROLES, ReadingOrderCalculator
from docyx.analysis.tables import TableAnalyzer
from docyx.analysis.visual import VisualAnalyzer
from docyx.core.constants import SCALE
from docyx.core.metadata import ProvenanceSource
from docyx.pdf.renderer import PDFRenderer
from docyx.pdf.text_extractor import NativeTextExtractor
from docyx.pipeline.gate import TextLayerGate
from docyx.schema.errors import PageIssue
from docyx.schema.models import Document, Element, Page, PageStatus, TextLayout


class DocyxPipeline:
    def __init__(
        self,
        layout_analyzer: Optional[LayoutAnalyzer] = None,
        table_analyzer: Optional[TableAnalyzer] = None,
        visual_analyzer: Optional[VisualAnalyzer] = None,
        ocr_analyzer: Optional[OCRAnalyzer] = None,
        ocr_repair: bool = False,
    ):
        self.gate = TextLayerGate()
        self.layout_analyzer = layout_analyzer or LayoutAnalyzer()
        self.table_analyzer = table_analyzer or TableAnalyzer()
        self.visual_analyzer = visual_analyzer or VisualAnalyzer()
        # No default: OCR must be opt-in, or `exact` silently becomes
        # `inferred` for anyone with tesseract installed.
        self.ocr_analyzer = ocr_analyzer
        # Off by default: replacing exact text with inferred text is a choice.
        self.ocr_repair = ocr_repair

    def process(
        self,
        file_path_or_stream: Union[str, bytes],
        document_id: str,
        pages: Optional[Iterable[int]] = None,
    ) -> Document:
        """Extract a document, or only the pages named in `pages` (0-based).

        `Page.page_number` stays absolute, so a selected page still says where
        it came from.
        """
        renderer = PDFRenderer(file_path_or_stream)
        try:
            extractor = renderer.text_extractor()
            doc_model = Document(
                document_id=document_id,
                filename=Path(file_path_or_stream).name
                if isinstance(file_path_or_stream, (str, Path))
                else None,
                # The document's own length, not len(pages).
                page_count=renderer.page_count(),
                pages=[],
            )

            wanted = range(renderer.page_count()) if pages is None else pages
            for page_num in wanted:
                doc_model.pages.append(self._safe_page(renderer, extractor, page_num))

            return doc_model
        finally:
            renderer.close()

    def _safe_page(
        self, renderer: PDFRenderer, extractor: NativeTextExtractor, page_num: int
    ) -> Page:
        """Isolate a failure to one page, never the whole document."""
        try:
            return self._process_page(renderer, extractor, page_num)
        except Exception as exc:  # noqa: BLE001 — the page is the blast radius
            return Page(
                page_number=page_num + 1,
                status=PageStatus.FAILED,
                width=0,
                height=0,
                source_type="unknown",
                errors=[
                    PageIssue(
                        code="PAGE_UNREADABLE",
                        stage="page_processing",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                ],
            )

    def _repairs(self, gate_result) -> bool:
        return (
            self.ocr_repair
            and self.ocr_analyzer is not None
            and gate_result.warning is not None
            and gate_result.warning.code in REPAIRABLE_CODES
        )

    def _process_page(
        self, renderer: PDFRenderer, extractor: NativeTextExtractor, page_num: int
    ) -> Page:
        gate_result = self.gate.check_page(renderer.text_document(), page_num)

        # Render unconditionally — visual detection never depends on the gate.
        image_bytes = renderer.render_page(page_num)

        # Detection runs on every page regardless of the gate outcome. A single
        # detector blowing up must not cost us the rest of the page.
        warnings: List[PageIssue] = []
        # The text layer may be present but garbled (§18.3) — that is a warning
        # on an otherwise usable page, not a gate failure.
        if gate_result.warning:
            warnings.append(gate_result.warning)

        detected: List[Element] = []
        for stage, run in (
            ("layout_detection", self.layout_analyzer.analyze),
            ("table_detection", self.table_analyzer.analyze),
            ("visual_detection", self.visual_analyzer.analyze),
        ):
            elements, warning = _safely(stage, run, image_bytes, page_num)
            detected.extend(elements)
            if warning:
                warnings.append(warning)

        width, height = renderer.page_size(page_num)

        if not gate_result.passed:
            source_type = "scanned" if renderer.has_images(page_num) else "empty"

            recognised: List[Element] = []
            if self.ocr_analyzer is not None:
                recognised, ocr_warning = _safely(
                    "ocr", self.ocr_analyzer.analyze, image_bytes, page_num
                )
                if ocr_warning:
                    warnings.append(ocr_warning)

            if recognised:
                return Page(
                    page_number=page_num + 1,
                    # Never OK: §24 makes a text layer the condition for that.
                    status=PageStatus.PARTIAL,
                    width=width,
                    height=height,
                    source_type=source_type,
                    # The gate error becomes a warning: an error on a page
                    # with content makes callers discard usable output.
                    warnings=warnings
                    + [
                        PageIssue(
                            code="OCR_TEXT",
                            stage="ocr",
                            message=(
                                f"page has no text layer; {len(recognised)} lines were "
                                "recognised from the rendered image and are `inferred`, "
                                "not read from the document"
                            ),
                        )
                    ],
                    elements=_assemble(recognised, detected),
                )

            return Page(
                page_number=page_num + 1,
                status=PageStatus.FAILED,
                width=width,
                height=height,
                source_type=source_type,
                errors=[gate_result.error],
                warnings=warnings,
                diagnostic_elements=detected,
            )

        try:
            native = extractor.extract_page(page_num)
        except Exception as exc:  # noqa: BLE001 — see _safe_page
            return Page(
                page_number=page_num + 1,
                status=PageStatus.FAILED,
                width=width,
                height=height,
                source_type="born_digital",
                errors=[
                    PageIssue(
                        code="EXTRACTION_FAILED",
                        stage="text_extraction",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                ],
                warnings=warnings,
                diagnostic_elements=detected,
            )

        # Text layer present but stored in the wrong order. Reading the
        # pixels sidesteps it: the rendering shows the intended order.
        if self._repairs(gate_result):
            repaired, warning = _safely(
                "ocr", self.ocr_analyzer.analyze, image_bytes, page_num
            )
            if warning:
                warnings.append(warning)
            if repaired:
                return Page(
                    page_number=page_num + 1,
                    # Already PARTIAL from the warning; this costs no status.
                    status=PageStatus.PARTIAL,
                    width=width,
                    height=height,
                    warnings=warnings
                    + [
                        PageIssue(
                            code="OCR_REPAIRED",
                            stage="ocr",
                            message=(
                                f"text layer was {gate_result.warning.code}; elements hold "
                                f"{len(repaired)} recognised lines (`inferred`) and the "
                                "native text is kept in diagnostic_elements"
                            ),
                        )
                    ],
                    elements=_assemble(repaired, detected),
                    # Nothing is discarded: the native text is still exact.
                    diagnostic_elements=native,
                )

        elements = _assemble(native, detected)
        return Page(
            page_number=page_num + 1,
            # Text came through, but a detector dropped out — the page is usable
            # yet incomplete, which is exactly what PARTIAL is for.
            status=PageStatus.PARTIAL if warnings else PageStatus.OK,
            width=width,
            height=height,
            warnings=warnings,
            elements=elements,
        )


#: Warnings OCR can fix. TEXT_LAYER_SUSPECT is excluded: there the glyphs
#: themselves may be undecodable, so OCR might not help.
REPAIRABLE_CODES = frozenset({"RTL_VISUAL_ORDER", "COMBINING_MARK_ORDER"})


def _safely(
    stage: str,
    run: Callable[..., List[Element]],
    image_bytes: bytes,
    page_num: int,
) -> Tuple[List[Element], Optional[PageIssue]]:
    try:
        return run(image_bytes, page_num=page_num), None
    except Exception as exc:  # a detector failure degrades the page, never the document
        return [], PageIssue(code="STAGE_FAILED", stage=stage, message=str(exc))


def _assemble(text: List[Element], detected: List[Element]) -> List[Element]:
    """Enrich text with the detections, order it, and fill table cells.

    One path for every source of text. The OCR branches used to call only the
    ordering step, so `--ocr --layout --tables` produced regions that typed
    nothing, no alignment, and table cells with null text — while the same
    flags on a born-digital page populated all three.
    """
    _assign_roles(text, detected)
    _assign_alignment(text, detected)
    elements = ReadingOrderCalculator.calculate(text + detected)
    _populate_cell_text(elements)
    return elements


def _assign_roles(text: List[Element], detected: List[Element]) -> None:
    """Give each line the semantic type of the region containing it (§8).

    The smallest containing region wins, since regions nest. Lines in no
    region keep `text`: a model that misses a region must not relabel prose.
    """
    regions = [
        el
        for el in detected
        if el.provenance.source is ProvenanceSource.LAYOUT_MODEL
        and el.type in TEXT_ROLES
    ]
    if not regions:
        return

    # Smallest first: the innermost region is the specific one.
    regions.sort(key=lambda el: el.geometry.bbox.width * el.geometry.bbox.height)
    for line in text:
        box = line.geometry.bbox
        cx, cy = box.x + box.width / 2, box.y + box.height / 2
        for region in regions:
            if region.geometry.bbox.contains(cx, cy):
                line.type = region.type
                break


#: A line counts as flush with its region's edge within this many 150-DPI px.
#: Justified text is not pixel-perfect and glyph advances overshoot slightly.
FLUSH_TOLERANCE = 4.0

#: Alignment needs a block with a shape. Two lines can show any two arbitrary
#: edges; three is the smallest sample where a repeated edge means something.
MIN_LINES_FOR_ALIGNMENT = 3


def _assign_alignment(text: List[Element], detected: List[Element]) -> None:
    """Measure each line's alignment against its layout region (§7).

    Here rather than in the extractor because it needs a real paragraph box;
    PyMuPDF blocks are not paragraphs. None without a layout model.
    """
    # Every region, including `text_region`, which is the paragraph box here.
    regions = [
        el for el in detected if el.provenance.source is ProvenanceSource.LAYOUT_MODEL
    ]
    if not regions:
        return

    regions.sort(key=lambda el: el.geometry.bbox.width * el.geometry.bbox.height)
    members: dict = {}
    for line in text:
        box = line.geometry.bbox
        cx, cy = box.x + box.width / 2, box.y + box.height / 2
        for region in regions:
            if region.geometry.bbox.contains(cx, cy):
                members.setdefault(id(region), []).append(line)
                break

    for lines in members.values():
        if len(lines) < MIN_LINES_FOR_ALIGNMENT:
            continue
        # Modal edges, not the bbox: a short last line must not move them.
        left = _modal_edge([el.geometry.bbox.x for el in lines], widest=min)
        right = _modal_edge([el.geometry.bbox.x1 for el in lines], widest=max)
        # Justification is a block property: in a centred block the widest
        # line is flush both sides and would claim `justify` alone.
        flush_both = sum(
            1
            for el in lines
            if abs(el.geometry.bbox.x - left) <= FLUSH_TOLERANCE
            and abs(el.geometry.bbox.x1 - right) <= FLUSH_TOLERANCE
        )
        for line in lines:
            if line.layout is None:
                line.layout = TextLayout()
            line.layout.alignment = _alignment(
                line.geometry.bbox, left, right, justified=flush_both >= 2
            )


def _modal_edge(values: List[float], widest) -> float:
    """The edge most lines share. `widest` (min for left, max for right)
    breaks ties toward the paragraph's true extent."""
    counts: dict = {}
    for value in values:
        key = round(value)
        counts[key] = counts.get(key, 0) + 1
    best = max(counts.values())
    return float(widest(k for k, n in counts.items() if n == best))


def _alignment(box, left: float, right: float, justified: bool) -> Optional[str]:
    flush_left = abs(box.x - left) <= FLUSH_TOLERANCE
    flush_right = abs(box.x1 - right) <= FLUSH_TOLERANCE
    symmetric = abs((box.x - left) - (right - box.x1)) <= FLUSH_TOLERANCE

    if flush_left and flush_right:
        return "justify" if justified else "center"
    if symmetric and not (flush_left or flush_right):
        return "center"
    if flush_left:
        return "left"
    if flush_right:
        return "right"
    return None


def _populate_cell_text(elements: List[Element]) -> None:
    """Join native text into detected table cells by position.

    A table model produces geometry only and never reads pixels as text, so
    cell text stays exact. Lines also remain in `elements`; the table is a
    view over the same content.
    """
    cells = [
        cell
        for element in elements
        if element.type == "table"
        for cell in element.children
        if cell.type == "table_cell"
    ]
    if not cells:
        return

    # TEXT_ROLES, not "text": _assign_roles may have retyped a line inside
    # the table to list_item or caption, and those cells would come back null.
    lines = [el for el in elements if el.type in TEXT_ROLES and el.text]
    for cell in cells:
        box = cell.geometry.bbox
        inside = [
            line
            for line in lines
            if box.contains(
                line.geometry.bbox.x + line.geometry.bbox.width / 2,
                line.geometry.bbox.y + line.geometry.bbox.height / 2,
            )
        ]
        inside.sort(key=lambda el: (el.reading_order is None, el.reading_order or 0))
        cell.text = " ".join(line.text.strip() for line in inside) or None
