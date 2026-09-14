from typing import Callable, Iterable, List, Optional, Tuple, Union

from docyx.analysis.layout import LayoutAnalyzer
from docyx.analysis.reading_order import ReadingOrderCalculator
from docyx.analysis.tables import TableAnalyzer
from docyx.analysis.visual import VisualAnalyzer
from docyx.core.constants import SCALE
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
    ):
        self.gate = TextLayerGate()
        self.layout_analyzer = layout_analyzer or LayoutAnalyzer()
        self.table_analyzer = table_analyzer or TableAnalyzer()
        self.visual_analyzer = visual_analyzer or VisualAnalyzer()

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
            extractor = NativeTextExtractor(renderer.doc)
            doc_model = Document(document_id=document_id, pages=[])

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

    def _process_page(
        self, renderer: PDFRenderer, extractor: NativeTextExtractor, page_num: int
    ) -> Page:
        gate_result = self.gate.check_page(renderer.doc, page_num)

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
            return Page(
                page_number=page_num + 1,
                status=PageStatus.FAILED,
                width=width,
                height=height,
                # The field exists to mark exactly this page. It defaulted to
                # born_digital and was never assigned, so the one case it was
                # for reported the opposite of the truth.
                source_type="scanned" if renderer.has_images(page_num) else "empty",
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
