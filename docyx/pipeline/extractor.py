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
from docyx.schema.models import Document, Element, Page, PageStatus


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
        # No default recogniser. OCR needs a binary this project does not ship,
        # and a pipeline that silently degrades exact text to inferred text
        # because someone happened to have tesseract installed would make the
        # confidence model unreadable. Opt in explicitly.
        self.ocr_analyzer = ocr_analyzer
        # Off by default: replacing exact text with inferred text is never the
        # obvious call, even when the exact text is demonstrably misordered.
        self.ocr_repair = ocr_repair

    def process(
        self,
        file_path_or_stream: Union[str, bytes],
        document_id: str,
        pages: Optional[Iterable[int]] = None,
    ) -> Document:
        """Extract a document, or just the pages named in `pages` (0-based).

        Page selection is not an optimisation detail: a page of an 800-page
        report costs the whole report without it, which makes large documents
        impractical to evaluate or to serve a single page from. `Page.page_number`
        stays absolute (1-based) so a selected page still says where it came
        from; only the list is shortened.
        """
        renderer = PDFRenderer(file_path_or_stream)
        try:
            extractor = renderer.text_extractor()
            doc_model = Document(
                document_id=document_id,
                filename=Path(file_path_or_stream).name
                if isinstance(file_path_or_stream, (str, Path))
                else None,
                # The document's own length, not len(pages): with `pages` set,
                # those differ, and a caller must be able to tell a 3-page
                # document from three pages of a 300-page one.
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
        """One unreadable page must cost that page, not the document.

        "Mixed documents are normal. Partial results, never reject the whole
        document" only held for the three detectors, which `_safely` wraps.
        Rendering and text extraction were unguarded, so a single corrupt page
        raised out of `process()` and took every other page of a 200-page
        report with it.
        """
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
            # The field exists to mark exactly this page. It defaulted to
            # born_digital and was never assigned, so the one case it was
            # for reported the opposite of the truth.
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
                    # Never OK. §24 makes a machine-readable text layer the
                    # condition for `ok`, and recognised text does not become
                    # one by being good — every character is `inferred`, so the
                    # page is usable but not authoritative. PARTIAL says both.
                    status=PageStatus.PARTIAL,
                    width=width,
                    height=height,
                    source_type=source_type,
                    # The gate error becomes a warning here: it is still true
                    # that the page has no text layer, but it no longer means
                    # the page produced nothing, and an `error` on a page with
                    # content would make callers discard usable output.
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
                    elements=ReadingOrderCalculator.calculate(recognised + detected),
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

        # The text layer is present and readable, but stored in an order the
        # document does not mean — visual order for RTL, glyph order for Indic.
        # Both are documented as unrecoverable FROM THE TEXT LAYER; reading the
        # pixels sidesteps the question, because the rendered page shows the
        # text the way a human reads it. Measured: OCR returns correct Bengali
        # and Arabic where the native layer returns scrambled.
        if self._repairs(gate_result):
            repaired, warning = _safely(
                "ocr", self.ocr_analyzer.analyze, image_bytes, page_num
            )
            if warning:
                warnings.append(warning)
            if repaired:
                return Page(
                    page_number=page_num + 1,
                    # Already PARTIAL from the gate warning, so preferring OCR
                    # costs no status: the page was never going to be `ok`.
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
                    elements=ReadingOrderCalculator.calculate(repaired + detected),
                    # Nothing is discarded. The native text is exact — it is
                    # only its ORDER that is wrong — so a consumer that can
                    # undo the reordering itself still has everything it needs.
                    diagnostic_elements=native,
                )

        _assign_roles(native, detected)
        elements = ReadingOrderCalculator.calculate(native + detected)
        _populate_cell_text(elements)
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


#: Gate warnings that OCR can actually fix. Both mean "every character is
#: present and correct, but in the wrong order" — which is exactly what
#: re-reading the rendered page recovers. TEXT_LAYER_SUSPECT is deliberately
#: absent: there the characters themselves are wrong, and OCR might help or
#: might not, so it is not a case this can decide unattended.
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


def _assign_roles(text: List[Element], detected: List[Element]) -> None:
    """Give each line the semantic type of the region containing it (§8).

    Without this a layout model produces boxes nobody consumes: the regions sit
    beside the text as separate elements, and every line stays `text` whatever
    the model decided. §5's example shows `"type": "title"` ON the text, which
    is the useful form — a consumer wants "this line is a heading", not "there
    is a heading-shaped rectangle somewhere near this line".

    The SMALLEST containing region wins. Regions nest — a caption sits inside
    the column region around it — and the innermost is the specific one.

    Lines inside no region keep `text`. A model that misses a region must not
    silently retype prose as something else, and roughly a fifth of lines land
    outside every detection on a real page.
    """
    regions = [
        el
        for el in detected
        if el.provenance.source is ProvenanceSource.LAYOUT_MODEL
        and el.type in TEXT_ROLES
    ]
    if not regions:
        return

    # Smallest first, so the first hit is the innermost region.
    regions.sort(key=lambda el: el.geometry.bbox.width * el.geometry.bbox.height)
    for line in text:
        box = line.geometry.bbox
        cx, cy = box.x + box.width / 2, box.y + box.height / 2
        for region in regions:
            if region.geometry.bbox.contains(cx, cy):
                line.type = region.type
                break


def _populate_cell_text(elements: List[Element]) -> None:
    """Fill detected table cells with the native text falling inside them.

    A table model produces geometry only - it has no idea what the cells say,
    and it never reads pixels as text. The native text layer stays the sole
    authority for content, so the two are joined by position: a line belongs to
    the cell containing its centre. That keeps cell text at confidence 1.0 /
    exact, and keeps the no-OCR contract intact.

    Lines stay in `elements` as well as in the cell. The table is a container
    view over the same content, not a replacement, and removing them would
    strip them out of reading order.
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

    lines = [el for el in elements if el.type == "text" and el.text]
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
