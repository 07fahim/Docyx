"""Write a document as a directory tree rather than one JSON blob (§4).

    document.json                  metadata + a page index, no elements
    pages/page_001.json            one page with all its elements
    tables/page_003_table_01.json  one table, cells and all
    figures/page_004_figure_01.png cropped from the rendered page

The index deliberately excludes elements: writing them here too would put
every element in the directory twice.
"""

import json
from pathlib import Path
from typing import List

import cv2
import numpy as np

from docyx.pdf.renderer import PDFRenderer
from docyx.schema.models import Document, Element, Page

#: `picture` is DocLayNet's name, `figure` the visual heuristic's.
FIGURE_TYPES = frozenset({"figure", "picture"})


def write_bundle(document: Document, pdf_path: str, target: Path) -> List[Path]:
    """Write the tree. Returns every path written, in order."""
    target.mkdir(parents=True, exist_ok=True)
    written = [_write_index(document, target)]

    for page in document.pages:
        written.append(
            _dump(target / "pages" / f"page_{page.page_number:03d}.json", page)
        )
        written.extend(_write_tables(page, target))

    written.extend(_write_figures(document, pdf_path, target))
    return written


def _write_index(document: Document, target: Path) -> Path:
    index = {
        "schema_version": document.schema_version,
        "document_id": document.document_id,
        "filename": document.filename,
        "page_count": document.page_count,
        "pages": [
            {
                "page_number": page.page_number,
                "status": page.status.value,
                "source_type": page.source_type,
                "file": f"pages/page_{page.page_number:03d}.json",
                "elements": len(page.elements),
                "issues": sorted({i.code for i in page.warnings + page.errors}),
            }
            for page in document.pages
        ],
    }
    path = target / "document.json"
    path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _write_tables(page: Page, target: Path) -> List[Path]:
    tables = [el for el in page.elements if el.type == "table"]
    return [
        _dump(
            target / "tables" / f"page_{page.page_number:03d}_table_{idx + 1:02d}.json",
            table,
        )
        for idx, table in enumerate(tables)
    ]


def _write_figures(document: Document, pdf_path: str, target: Path) -> List[Path]:
    """Crop each detected figure from its page, re-rendering to do so.

    A 150-DPI RGB page is ~6 MB, too much to hold through the pipeline.
    """
    wanted = {
        page.page_number: [el for el in page.elements if el.type in FIGURE_TYPES]
        for page in document.pages
    }
    if not any(wanted.values()):
        return []

    written: List[Path] = []
    with PDFRenderer(pdf_path) as renderer:
        for page_number, figures in wanted.items():
            if not figures:
                continue
            image = cv2.imdecode(
                np.frombuffer(renderer.render_page(page_number - 1), np.uint8),
                cv2.IMREAD_COLOR,
            )
            for idx, figure in enumerate(figures):
                crop = _crop(image, figure)
                if crop is None:
                    continue
                path = (
                    target
                    / "figures"
                    / f"page_{page_number:03d}_figure_{idx + 1:02d}.png"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(path), crop)
                written.append(path)
    return written


def _crop(image, element: Element):
    """Clamped to the page: numpy slicing an off-page box returns empty."""
    height, width = image.shape[:2]
    box = element.geometry.bbox
    x0, y0 = max(int(box.x), 0), max(int(box.y), 0)
    x1, y1 = min(int(box.x1), width), min(int(box.y1), height)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return image[y0:y1, x0:x1]


def _dump(path: Path, model) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        model.model_dump_json(indent=2, exclude_none=True), encoding="utf-8"
    )
    return path
