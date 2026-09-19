"""Line-granularity extraction and band-aware reading order.

Both fixes come from the same real-world failure: on a real paper page the
output read "We employ a residual connection [ ] around each of 11 the two
sub-layers" — inline citations and maths scattered because extraction was
per-*span* and ordering compared raw y.
"""

import fitz

from docyx.analysis.reading_order import ReadingOrderCalculator
from docyx.core.geometry import BoundingBox, Geometry
from docyx.core.metadata import Confidence, ConfidenceType, Provenance, ProvenanceSource
from docyx.export import to_markdown
from docyx.pdf.text_extractor import NativeTextExtractor, _dominant_typography
from docyx.pipeline.extractor import DocyxPipeline
from docyx.pipeline.extractor import DocyxPipeline
from docyx.schema.models import Element


class _FakePage:
    """A page whose text dict has the span structure PyMuPDF produces for a
    line containing an inline citation: the citation is its own span, and the
    inter-word spaces are spans too."""

    def __init__(self, blocks):
        self._blocks = blocks

    def get_text(self, _kind):
        return {"blocks": self._blocks}


class _FakeDoc:
    def __init__(self, page):
        self._page = page

    def __getitem__(self, _n):
        return self._page


def _span(text, size=10.0, font="Body", bbox=(0, 0, 10, 10)):
    return {"text": text, "size": size, "font": font, "flags": 0, "color": 0, "bbox": bbox}


def _line(spans, bbox=(10.0, 20.0, 400.0, 32.0)):
    return {"bbox": bbox, "spans": spans}


def test_spans_join_into_one_line_element_with_citation_in_place():
    page = _FakePage([
        {
            "type": 0,
            "lines": [
                _line([
                    _span("We employ a residual connection ["),
                    _span("11", size=7.0, font="Superscript"),
                    _span("] around each of"),
                ])
            ],
        }
    ])

    elements = NativeTextExtractor(_FakeDoc(page)).extract_page(0)

    assert len(elements) == 1, "one element per line, not per span"
    assert elements[0].text == "We employ a residual connection [11] around each of"


def test_whitespace_only_spans_are_preserved_not_dropped():
    """PyMuPDF emits inter-word gaps as their own spans. Skipping blank spans —
    which the per-span version did — runs the words together."""
    page = _FakePage([
        {"type": 0, "lines": [_line([_span("The"), _span(" "), _span("encoder")])]}
    ])

    elements = NativeTextExtractor(_FakeDoc(page)).extract_page(0)

    assert elements[0].text == "The encoder"


def test_blank_line_is_still_skipped():
    page = _FakePage([{"type": 0, "lines": [_line([_span("  "), _span("\t")])]}])
    assert NativeTextExtractor(_FakeDoc(page)).extract_page(0) == []


def test_non_text_blocks_are_ignored():
    page = _FakePage([{"type": 1, "lines": [_line([_span("image")])]}])
    assert NativeTextExtractor(_FakeDoc(page)).extract_page(0) == []


def test_typography_comes_from_the_longest_span_not_the_first():
    """A leading superscript or drop cap must not misreport the whole line,
    since the heading heuristic keys on font size."""
    typography = _dominant_typography([
        _span("1", size=6.0, font="Tiny"),
        _span("This is the actual body of the line", size=11.0, font="Body"),
    ])

    assert typography.font_size == 11.0
    assert typography.font_family == "Body"


def test_typography_is_none_when_a_line_has_no_real_text():
    assert _dominant_typography([_span("   ")]) is None


def _text_element(id_, x, y, height=12.0):
    return Element(
        id=id_,
        type="text",
        geometry=Geometry(bbox=BoundingBox(x=x, y=y, width=50.0, height=height)),
        confidence=Confidence(value=1.0, type=ConfidenceType.EXACT),
        provenance=Provenance(source=ProvenanceSource.NATIVE_PDF),
        text=id_,
    )


def test_run_in_heading_precedes_its_paragraph_despite_a_higher_bbox():
    """The exact failure: a bold run-in heading has a different ascender, so its
    bbox y is fractionally *larger* than the text it introduces. Sorting on raw
    y then puts the heading second."""
    body = _text_element("body", x=130.0, y=88.18)
    heading = _text_element("heading", x=72.0, y=88.23)  # lower on the page by 0.05

    ordered = ReadingOrderCalculator.calculate([body, heading])

    assert [el.id for el in ordered] == ["heading", "body"]


def test_genuinely_separate_lines_still_order_top_to_bottom():
    """Banding must not swallow real line breaks."""
    first = _text_element("first", x=200.0, y=100.0)
    second = _text_element("second", x=10.0, y=140.0)

    ordered = ReadingOrderCalculator.calculate([second, first])

    assert [el.id for el in ordered] == ["first", "second"]


def test_run_in_heading_from_a_real_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Encoder:", fontname="hebo", fontsize=11)
    page.insert_text((130, 100), "The encoder is composed of layers.", fontname="helv", fontsize=11)
    pdf = tmp_path / "runin.pdf"
    doc.save(str(pdf))
    doc.close()

    md = to_markdown(DocyxPipeline().process(str(pdf), "d"))

    assert md.index("Encoder:") < md.index("The encoder is composed")


# --- mixed typography within a line (§7) -----------------------------------


def test_a_bold_run_in_a_normal_line_is_not_lost():
    """A line's typography is its DOMINANT span, which is lossy when the line
    mixes styles: "Note: and the rest of the sentence" reports bold=False
    because the normal run is longer, and the bold disappears entirely.

    Measured on the corpus: 3.6% of lines mix bold with non-bold, 9.0% mix any
    two styles.
    """
    doc = fitz.open()
    page = doc.new_page()
    writer = fitz.TextWriter(page.rect)
    where = writer.append((72, 100), "Note: ", font=fitz.Font("tibo"), fontsize=11)
    # Continue from where the bold run ended, so PyMuPDF sees ONE line with two
    # spans rather than two lines that happen to share a baseline.
    writer.append(where[1], "ordinary body text follows here", font=fitz.Font("tiro"), fontsize=11)
    writer.write_text(page)
    path = doc.write()
    doc.close()

    page_out = DocyxPipeline().process(path, "d", pages=[0]).pages[0]
    line = next(el for el in page_out.elements if el.text and "Note" in el.text)

    assert len(line.children) == 2, "a mixed line must keep its runs"
    assert [c.type for c in line.children] == ["text_span", "text_span"]
    fonts = [c.typography.font_family for c in line.children]
    assert fonts[0] != fonts[1], "the two runs must differ in style"
    assert "".join(c.text for c in line.children) == line.text


def test_a_uniform_line_carries_no_runs():
    """Giving every uniform line a child restating its own typography would
    roughly double the output to say nothing. Empty children means "the line's
    own typography is the whole story"."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "All one style here", fontsize=11)
    path = doc.write()
    doc.close()

    page_out = DocyxPipeline().process(path, "d", pages=[0]).pages[0]

    assert all(el.children == [] for el in page_out.elements if el.text)


def test_style_runs_are_never_numbered_in_reading_order():
    """A line and its own fragments must not occupy separate positions."""
    doc = fitz.open()
    page = doc.new_page()
    writer = fitz.TextWriter(page.rect)
    where = writer.append((72, 100), "Bold ", font=fitz.Font("tibo"), fontsize=11)
    writer.append(where[1], "normal", font=fitz.Font("tiro"), fontsize=11)
    writer.write_text(page)
    path = doc.write()
    doc.close()

    page_out = DocyxPipeline().process(path, "d", pages=[0]).pages[0]
    line = next(el for el in page_out.elements if el.children)

    assert line.reading_order == 1
    assert all(child.reading_order is None for child in line.children)
