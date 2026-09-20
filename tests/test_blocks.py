"""Export in the flat block-annotation shape.

Compatibility is a claim, and these are what make it checkable: the entry is
closed, the categories are case-sensitive, and the coordinate origin is 1 --
so anything this writes has to survive a consuming tool's own checks, which
`validate` mirrors.
"""

import json

import fitz
import pytest

from docyx.analysis.layout import LayoutAnalyzer, LayoutDetection
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.export.blocks import (
    CATEGORIES,
    page_entries,
    validate,
    write_blocks,
)
from docyx.pipeline.extractor import DocyxPipeline
from docyx.schema.models import Element, Page, PageStatus

#: The ten category names this format carries. Nothing else may appear.
ACCEPTED = {
    "Title", "Section-header", "Text", "List-item", "Table",
    "Picture", "Caption", "Footnote", "Page-header", "Page-footer",
}


@pytest.fixture
def pdf(tmp_path):
    doc = fitz.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text((72, 100), f"First line of page {i}.", fontsize=11)
        page.insert_text((72, 130), f"Second line of page {i}.", fontsize=11)
    path = tmp_path / "book.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def element(type_, x=10, y=10, w=100, h=20, text="words", source=ProvenanceSource.NATIVE_PDF):
    return Element(
        id=f"e_{type_}_{x}_{y}",
        type=type_,
        geometry=Geometry(bbox=BoundingBox(x=x, y=y, width=w, height=h)),
        confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
        provenance=Provenance(source=source),
        text=text,
    )


def page_of(*elements, width=600, height=800):
    return Page(page_number=1, status=PageStatus.OK, width=width, height=height,
                elements=list(elements))


def test_every_mapped_category_is_one_the_tool_accepts():
    """Capitalisation is significant: "Text" works, "text" and "TEXT" do not."""
    assert set(CATEGORIES.values()) <= ACCEPTED


def test_an_entry_holds_exactly_category_bbox_and_text():
    """"Nothing else is allowed in an entry. Adding anything extra will stop
    the file from saving." So confidence and provenance must not leak out."""
    entries, dropped = page_entries(page_of(element("text")))

    assert entries == [{"category": "Text", "bbox": [10, 10, 110, 30], "text": "words"}]
    assert dropped == []


def test_a_picture_carries_no_text_and_everything_else_must():
    """Its validator calls words on a Picture an "Unexpected field", and a
    missing text anywhere else a "Missing field"."""
    entries, dropped = page_entries(page_of(
        element("figure", text=None),
        element("text", y=200, text="   "),
    ))

    assert entries == [{"category": "Picture", "bbox": [10, 10, 110, 30]}]
    assert "no text" in dropped[0]


def test_coordinates_are_one_indexed_and_never_leave_the_page():
    """"No number is ever zero or below. The very top-left of the picture
    counts as 1, not 0." Real pages produce negative x: on `.corpus/wiki_ar.pdf`
    p6 a justified RTL line starts at -11.9."""
    entries, _ = page_entries(page_of(element("text", x=-11.9, y=-3, w=40, h=10)))

    assert entries[0]["bbox"] == [1, 1, 28, 7]
    assert min(entries[0]["bbox"]) >= 1


def test_a_clamp_not_a_shift():
    """Adding one to every coordinate would satisfy the origin rule and move
    every box off its words."""
    # The manual's own worked example: page 43, entry 1, on a 1253x1824 image.
    entries, _ = page_entries(page_of(element("text", x=153, y=146, w=968, h=442),
                                      width=1253, height=1824))

    assert entries[0]["bbox"] == [153, 146, 1121, 588]


def test_a_box_bigger_than_the_page_is_trimmed_to_it():
    entries, _ = page_entries(page_of(element("text", x=500, y=700, w=400, h=400),
                                      width=600, height=800))

    assert entries[0]["bbox"] == [500, 700, 600, 800]


def test_a_zero_height_element_still_gets_a_usable_box():
    """A rule has no height, and their validator rejects a box with no size."""
    entries, _ = page_entries(page_of(element("text", h=0, w=0)))

    left, top, right, bottom = entries[0]["bbox"]
    assert right > left and bottom > top


def test_validate_catches_what_their_save_would_reject():
    assert validate([{"category": "Nope", "bbox": [1, 1, 2, 2], "text": "x"}], 10, 10)
    assert validate([{"category": "Text", "bbox": [5, 1, 2, 2], "text": "x"}], 10, 10)
    assert validate([{"category": "Text", "bbox": [1, 5, 2, 2], "text": "x"}], 10, 10)
    assert validate([{"category": "Text", "bbox": [0, 1, 2, 2], "text": "x"}], 10, 10)
    assert validate([{"category": "Text", "bbox": [1, 1, 2], "text": "x"}], 10, 10)
    assert validate([{"category": "Picture", "bbox": [1, 1, 2, 2], "text": "x"}], 10, 10)
    assert validate([{"category": "Text", "bbox": [1, 1, 2, 2]}], 10, 10)
    assert validate([{"category": "Text", "bbox": [1, 1, 2, 2], "text": "x"}], 10, 10) == []


def test_a_formula_is_dropped_and_reported_rather_than_called_text():
    """DocLayNet has 11 classes and this tool accepts 10. Exporting a formula
    as Text would poison the labels the export exists to produce."""
    entries, dropped = page_entries(page_of(element("formula")))

    assert entries == []
    assert "no category in this format" in dropped[0]


# --- block granularity ------------------------------------------------------


def test_layout_regions_become_the_blocks_and_lines_supply_the_words():
    """Its blocks are paragraphs; Docyx extracts lines. With a layout model the
    regions are the paragraphs, so one entry covers the lines inside it."""
    region = element("text_region", x=0, y=0, w=300, h=100, text=None,
                     source=ProvenanceSource.LAYOUT_MODEL)
    first = element("text", x=10, y=10, w=200, h=20, text="first")
    first.reading_order = 1
    second = element("text", x=10, y=40, w=200, h=20, text="second")
    second.reading_order = 2

    entries, _ = page_entries(page_of(region, second, first))

    assert entries == [{"category": "Text", "bbox": [1, 1, 300, 100],
                        "text": "first second"}]


def test_a_line_outside_every_region_is_still_exported():
    """Dropping it produces the exact defect their review pass hunts for:
    a block on the page with no box at all."""
    region = element("text_region", x=0, y=0, w=300, h=100, text=None,
                     source=ProvenanceSource.LAYOUT_MODEL)
    inside = element("text", x=10, y=10, w=200, h=20, text="inside")
    outside = element("text", x=10, y=400, w=200, h=20, text="orphan")

    entries, _ = page_entries(page_of(region, inside, outside))

    assert [e["text"] for e in entries] == ["inside", "orphan"]


def test_without_a_layout_model_lines_are_the_blocks(pdf):
    page = DocyxPipeline().process(pdf, "x", pages=[0]).pages[0]

    entries, _ = page_entries(page)

    assert len(entries) == 2, "two lines, two entries"
    assert all(e["category"] == "Text" for e in entries)
    assert validate(entries, page.width, page.height) == []


def test_a_real_page_survives_their_own_checks(pdf):
    """The end-to-end claim: what this writes, that tool would accept."""
    def detector(image_bytes):
        return [LayoutDetection(bbox=BoundingBox(x=0, y=0, width=600, height=400),
                                score=0.9, label="text_region")]

    page = DocyxPipeline(layout_analyzer=LayoutAnalyzer(detector=detector)).process(
        pdf, "x", pages=[0]).pages[0]
    entries, _ = page_entries(page)

    assert entries, "the region should have produced a block"
    assert validate(entries, page.width, page.height) == []


# --- files ------------------------------------------------------------------


def test_images_and_annotations_are_written_with_matching_stems(pdf, tmp_path):
    """"The image of the page and its annotation belong together because they
    share a name, ignoring the ending." Flat, because it only reads files
    sitting directly inside the chosen folder."""
    out = tmp_path / "out"
    document = DocyxPipeline().process(pdf, "book")

    written = write_blocks(document, pdf, out)

    assert len(written) == 4, "two pages, an image and an annotation each"
    stems = sorted({p.stem for p in out.iterdir()})
    assert stems == ["book_page_0001", "book_page_0002"]
    for stem in stems:
        assert (out / f"{stem}.png").read_bytes()[:4] == b"\x89PNG"
        entries = json.loads((out / f"{stem}.json").read_text(encoding="utf-8"))
        assert entries and validate(entries, document.pages[0].width,
                                    document.pages[0].height) == []
