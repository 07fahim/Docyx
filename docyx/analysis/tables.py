from dataclasses import dataclass, field
from typing import Callable, List, Optional

from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Element, GridPosition


@dataclass
class CellDetection:
    """One cell, with its position in the table grid."""

    bbox: BoundingBox
    row: int
    column: int
    row_span: int = 1
    column_span: int = 1
    score: float = 1.0


@dataclass
class TableDetection:
    """A table region plus its recovered grid. `cells` may be empty when the
    detector found the table outline but could not resolve structure."""

    bbox: BoundingBox
    score: float
    cells: List[CellDetection] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return max((c.row + c.row_span for c in self.cells), default=0)

    @property
    def column_count(self) -> int:
        return max((c.column + c.column_span for c in self.cells), default=0)


Detector = Callable[[bytes], List[TableDetection]]


class TableAnalyzer:
    """Detects table regions and their cell grid from a rendered page image.

    The detector callable performs the actual table inference. When no detector
    is supplied, a deterministic heuristic stub is used (no detections) so the
    pipeline is testable without model weights.
    """

    ENGINE = "table-heuristic-v1"

    def __init__(self, detector: Optional[Detector] = None):
        self._detector = detector

    def analyze(self, image_bytes: bytes, page_num: int = 0) -> List[Element]:
        tables = self._detector(image_bytes) if self._detector else self._heuristic(image_bytes)
        elements = []
        for idx, table in enumerate(tables):
            table_id = f"page{page_num + 1}_table_{idx}"
            elements.append(
                Element(
                    id=table_id,
                    type="table",
                    geometry=Geometry(bbox=table.bbox),
                    confidence=Confidence(value=table.score, type=ConfidenceType.DETECTED),
                    provenance=Provenance(
                        source=ProvenanceSource.TABLE_MODEL,
                        engine=self.ENGINE,
                        raw_confidence=table.score,
                    ),
                    children=[self._cell(table_id, cell) for cell in table.cells],
                )
            )
        return elements

    def _cell(self, table_id: str, cell: CellDetection) -> Element:
        return Element(
            id=f"{table_id}_r{cell.row}_c{cell.column}",
            type="table_cell",
            geometry=Geometry(bbox=cell.bbox),
            confidence=Confidence(value=cell.score, type=ConfidenceType.DETECTED),
            provenance=Provenance(
                source=ProvenanceSource.TABLE_MODEL,
                engine=self.ENGINE,
                raw_confidence=cell.score,
            ),
            grid=GridPosition(
                row=cell.row,
                column=cell.column,
                row_span=cell.row_span,
                column_span=cell.column_span,
            ),
        )

    @staticmethod
    def _heuristic(image_bytes: bytes) -> List[TableDetection]:
        return []
