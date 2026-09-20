"""The page check report finds defects that validate cleanly."""

import pytest

from docyx.analysis.check import check_page
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.schema.models import Element, Page, PageStatus


def _element(id_, x, y, w, h, text="a line", type_="text"):
    return Element(
        id=id_,
        type=type_,
        geometry=Geometry(bbox=BoundingBox(x=x, y=y, width=w, height=h)),
        confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
        provenance=Provenance(source=ProvenanceSource.NATIVE_PDF),
        text=text,
    )


def _page(*elements):
    return Page(
        page_number=1, status=PageStatus.OK, width=1242, height=1754,
        elements=list(elements),
    )


def codes(page):
    return {f.code for f in check_page(page)}


def test_a_clean_page_reports_nothing():
    page = _page(_element("a", 100, 100, 400, 20), _element("b", 100, 140, 400, 20))
    assert check_page(page) == []


def test_a_box_with_no_height_is_reported():
    page = _page(_element("a", 100, 100, 400, 0))
    assert codes(page) == {"ZERO_SIZE_BOX"}
    assert check_page(page)[0].ids == ["a"]


def test_a_text_element_with_no_text_is_reported():
    page = _page(_element("a", 100, 100, 400, 20, text="   "))
    assert codes(page) == {"EMPTY_TEXT"}


def test_a_justified_rtl_line_left_of_zero_is_not_reported():
    """`wiki_ar.pdf` p6 has 31 of these and the worst is 11.9px out. Reporting
    them would put 31 findings on a page with nothing wrong with it."""
    page = _page(_element("a", -11.9, 100, 400, 20))
    assert check_page(page) == []


def test_a_box_far_off_the_page_is_reported():
    page = _page(_element("a", -300, 100, 400, 20))
    assert codes(page) == {"BOX_OFF_PAGE"}


def test_a_box_inside_another_of_the_same_type_is_reported():
    """The `/` date separators on `irs_f1040.pdf` p0 are extracted as their own
    lines wholly inside the wider line's box."""
    page = _page(
        _element("wide", 800, 130, 200, 17, text="Deceased MM DD YYYY"),
        _element("slash", 904, 130, 5, 17, text="/"),
    )
    assert codes(page) == {"STACKED_BOXES"}
    assert check_page(page)[0].ids == ["slash", "wide"]


def test_boxes_of_different_types_may_overlap():
    """A line sits inside its layout region by design, and reporting that
    would bury every real duplicate."""
    page = _page(
        _element("region", 100, 100, 400, 200, text="", type_="layout_region"),
        _element("line", 110, 110, 380, 20),
    )
    assert check_page(page) == []


@pytest.mark.parametrize("corpus,page_num", [("wiki_ar.pdf", 6), ("rfc2616.pdf", 12)])
def test_real_pages_stay_clean(corpus, page_num):
    """A check that fires on ordinary pages is a check nobody will read."""
    from pathlib import Path

    from docyx.pipeline.extractor import DocyxPipeline

    pdf = Path(".corpus") / corpus
    if not pdf.exists():
        pytest.skip(f"{pdf} is not in the corpus")
    page = DocyxPipeline().process(str(pdf), "check", pages=[page_num]).pages[0]
    assert check_page(page) == []
