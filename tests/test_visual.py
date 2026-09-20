"""Exercises the real OpenCV heuristic, not an injected stub."""

import cv2
import numpy as np
import pytest

from docyx.analysis.visual import VisualAnalyzer, reject_text_figures
from docyx.core.geometry import BoundingBox


def _png(draw) -> bytes:
    img = np.full((600, 800), 255, np.uint8)  # white page
    draw(img)
    return cv2.imencode(".png", img)[1].tobytes()


def test_blank_page_detects_nothing():
    assert VisualAnalyzer().analyze(_png(lambda img: None)) == []


def test_detects_a_horizontal_rule():
    # A long thin black line across most of the page width.
    elements = VisualAnalyzer().analyze(_png(lambda img: cv2.line(img, (40, 300), (760, 300), 0, 3)))

    rules = [el for el in elements if el.type == "rule"]
    assert len(rules) == 1
    box = rules[0].geometry.bbox
    assert box.width > 700
    assert box.height < 10
    assert 295 <= box.y <= 305


def test_detects_a_figure_block():
    elements = VisualAnalyzer().analyze(
        _png(lambda img: cv2.rectangle(img, (100, 100), (500, 400), 0, -1))
    )

    figures = [el for el in elements if el.type == "figure"]
    assert len(figures) == 1
    box = figures[0].geometry.bbox
    assert box.width > 350 and box.height > 250


def test_small_marks_are_below_the_figure_threshold():
    # A speck well under min_figure_area_ratio must not become a figure.
    elements = VisualAnalyzer().analyze(
        _png(lambda img: cv2.rectangle(img, (10, 10), (25, 25), 0, -1))
    )
    assert [el for el in elements if el.type == "figure"] == []


def test_undecodable_bytes_return_no_detections():
    assert VisualAnalyzer().analyze(b"not-an-image") == []


def test_dense_text_is_not_reported_as_a_figure():
    """The failure found on real documents: closing merges a paragraph into one
    blob that is figure-shaped, so a 114-page plain-text RFC reported 434
    figures. Text shatters into one component per glyph; figures do not."""
    img = np.full((600, 800), 255, np.uint8)
    # ~500 glyph-sized marks, the texture of a dense text block
    for row in range(120, 460, 16):
        for col in range(100, 640, 11):
            cv2.rectangle(img, (col, row), (col + 6, row + 10), 0, -1)
    png = cv2.imencode(".png", img)[1].tobytes()

    assert [el for el in VisualAnalyzer().analyze(png) if el.type == "figure"] == []


def test_a_solid_block_is_still_a_figure_alongside_text():
    """The filter must not cost us true positives."""
    img = np.full((600, 800), 255, np.uint8)
    for row in range(40, 120, 16):  # a band of text
        for col in range(100, 640, 11):
            cv2.rectangle(img, (col, row), (col + 6, row + 10), 0, -1)
    cv2.rectangle(img, (150, 250), (600, 520), 0, -1)  # a real figure
    png = cv2.imencode(".png", img)[1].tobytes()

    figures = [el for el in VisualAnalyzer().analyze(png) if el.type == "figure"]
    assert len(figures) == 1
    assert figures[0].geometry.bbox.width > 400


def test_component_density_threshold_is_tunable():
    img = np.full((600, 800), 255, np.uint8)
    for row in range(120, 460, 16):
        for col in range(100, 640, 11):
            cv2.rectangle(img, (col, row), (col + 6, row + 10), 0, -1)
    png = cv2.imencode(".png", img)[1].tobytes()

    permissive = VisualAnalyzer(max_component_density=10_000.0).analyze(png)
    assert [el for el in permissive if el.type == "figure"]


# --- the text-figure rejection, and what it must not take with it -----------
#
# `max_component_density` separates text from figures by counting connected
# components, which is a property of an alphabet with separated letters rather
# than of writing. Measured over real text lines, components per 10k pixels:
# latin 62.3 / 43.1, bengali 10.5, arabic 0.0 — against a threshold of 20. So
# Bengali and Arabic prose landed exactly where figures live and whole
# paragraphs were reported as pictures. These pin the fix, which overrules the
# pixels with the extracted lines.


def _element(kind, x, y, w, h, text=None):
    from docyx.core.geometry import Geometry
    from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
    from docyx.schema.models import Element

    return Element(
        id=f"{kind}_{x}_{y}",
        type=kind,
        text=text,
        geometry=Geometry(bbox=BoundingBox(x=x, y=y, width=w, height=h)),
        confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
        provenance=Provenance(source=ProvenanceSource.NATIVE_PDF),
    )


def test_a_figure_box_tiled_with_text_lines_is_not_a_figure():
    """The Bengali and Arabic case: cursive and joined scripts fragment too
    little for the pixel heuristic, so the lines have to say so instead."""
    figure = _element("figure", 100, 100, 400, 200)
    lines = [_element("text", 100, 100 + i * 40, 400, 36, "a line") for i in range(5)]

    kept = reject_text_figures([figure], lines)

    assert kept == []


def test_a_real_picture_survives_its_own_caption():
    """A caption inside the box must not turn a photograph into text."""
    figure = _element("figure", 100, 100, 400, 400)
    caption = _element("text", 100, 470, 400, 25, "Figure 1: a caption")

    assert reject_text_figures([figure], [caption]) == [figure]


def test_a_rule_inside_a_picture_is_image_content():
    """A lit facade, a horizon, the frame of the image itself. `wiki_bn` p30
    reported five of these, all inside photographs."""
    picture = _element("figure", 100, 100, 400, 400)
    horizon = _element("rule", 110, 300, 380, 3)

    kept = reject_text_figures([picture, horizon], [])

    assert kept == [picture]


def test_a_rule_inside_a_TABLE_survives():
    """The trade this must not make: `nasa_budget` p88's ruled table is caught
    as a figure, and suppressing inside it deleted the table's own bottom
    border — a missing rule is a worse defect than a spurious one."""
    table_ish = _element("figure", 100, 100, 400, 200)
    lines = [_element("text", 105, 105 + i * 60, 390, 30, "cell") for i in range(3)]
    border = _element("rule", 100, 295, 400, 2)

    kept = reject_text_figures([table_ish, border], lines)

    assert border in kept


def test_a_rule_inside_a_declared_image_is_rejected_without_any_figure():
    """The file says where its pictures are, and that beats the heuristic: the
    one survivor on `wiki_bn` p30 was a horizon in a photo whose sky is white,
    leaving too little ink to contour."""
    horizon = _element("rule", 110, 300, 380, 3)

    kept = reject_text_figures([horizon], [], images=[(100.0, 100.0, 500.0, 500.0)])

    assert kept == []
