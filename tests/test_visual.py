"""Exercises the real OpenCV heuristic, not an injected stub."""

import cv2
import numpy as np
import pytest

from docyx.analysis.visual import VisualAnalyzer


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
