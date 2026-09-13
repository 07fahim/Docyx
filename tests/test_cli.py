"""The command line entry point.

Exit codes are the contract for anything scripted, so they are tested harder
than the output formatting.
"""

import json
from pathlib import Path

import fitz
import pytest

from docyx.cli import main, parse_pages


@pytest.fixture
def text_pdf(tmp_path):
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 100), f"Page {i} has real text.", fontsize=11)
    path = tmp_path / "text.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def scanned_pdf(tmp_path):
    """One page of pixels and no text layer — fails the gate by design."""
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.open().new_page().get_pixmap()
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    path = tmp_path / "scanned.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.mark.parametrize(
    "spec,expected",
    [
        (None, None),
        ("3", [3]),
        ("0-4", [0, 1, 2, 3, 4]),
        ("0-2,9", [0, 1, 2, 9]),
        (" 1 , 3 ", [1, 3]),
    ],
)
def test_parse_pages(spec, expected):
    assert parse_pages(spec) == expected


def test_clean_document_exits_zero(text_pdf, capsys):
    assert main([str(text_pdf), "-q"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == "1.3"


def test_a_failed_page_exits_one(scanned_pdf, capsys):
    """A gate failure is a documented outcome, not a crash: the page is still
    in the output, carrying its reason."""
    assert main([str(scanned_pdf), "-q"]) == 1

    page = json.loads(capsys.readouterr().out)["pages"][0]
    assert page["status"] == "failed"
    assert page["errors"][0]["code"] == "NO_TEXT_LAYER"


def test_missing_file_exits_two_without_stopping_the_batch(text_pdf, tmp_path, capsys):
    out = tmp_path / "out"
    code = main([str(tmp_path / "nope.pdf"), str(text_pdf), "-o", str(out), "-q"])

    assert code == 2
    assert (out / "text.json").exists(), "the readable file must still be processed"


def test_strict_promotes_a_degraded_page(tmp_path):
    """Without --strict a `partial` page is a success: the text is usable and
    the warning says why it is suspect. --strict is for callers that want a
    hard gate instead."""
    # Mirrored delimiters are what visual-order storage actually looks like:
    # ']1[' where the document reads '[1]'. Writing them in logical order
    # produces a clean page and so tests nothing.
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "مرحبا ]1[ )2020(", fontsize=11,
                               fontfile=r"C:\Windows\Fonts\arial.ttf", fontname="ar")
    path = tmp_path / "rtl.pdf"
    doc.save(str(path))
    doc.close()

    assert main([str(path), "-q", "-o", str(tmp_path / "a.json")]) == 0
    assert main([str(path), "-q", "--strict", "-o", str(tmp_path / "b.json")]) == 1


def test_markdown_output_and_file_target(text_pdf, tmp_path):
    target = tmp_path / "out.md"
    assert main([str(text_pdf), "-f", "markdown", "-o", str(target), "-q"]) == 0
    assert "Page 0 has real text." in target.read_text(encoding="utf-8")


def test_several_pdfs_need_a_directory_not_a_file(text_pdf, tmp_path):
    """Writing every document to one path would silently keep only the last."""
    clash = tmp_path / "single.json"
    clash.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit):
        main([str(text_pdf), str(text_pdf), "-o", str(clash), "-q"])


def test_page_selection_limits_the_output(text_pdf, capsys):
    assert main([str(text_pdf), "--pages", "1", "-q"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert len(document["pages"]) == 1
    # page_number stays absolute so a selected page still says where it came from
    assert document["pages"][0]["page_number"] == 2
