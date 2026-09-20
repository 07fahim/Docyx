"""The command line entry point.

Exit codes are the contract for anything scripted, so they are tested harder
than the output formatting.
"""

import argparse
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
    assert json.loads(capsys.readouterr().out)["schema_version"] == "1.9"


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


# --- bundle export ---------------------------------------------------------


def test_bundle_writes_a_tree_with_an_index(text_pdf, tmp_path):
    out = tmp_path / "tree"
    assert main([str(text_pdf), "-f", "bundle", "-o", str(out), "-q"]) == 0

    index = json.loads((out / "document.json").read_text(encoding="utf-8"))
    assert index["filename"] == "text.pdf"
    assert index["page_count"] == 3
    assert [p["file"] for p in index["pages"]] == [
        "pages/page_001.json",
        "pages/page_002.json",
        "pages/page_003.json",
    ]
    assert all((out / p["file"]).exists() for p in index["pages"])


def test_the_index_holds_no_elements():
    """Writing the whole document there as well means every element exists
    twice in one directory, and the copies can disagree after an edit. The
    index answers "which pages need attention", which is what a top-level file
    is actually for."""
    from docyx.export.bundle import _write_index
    from docyx.schema.models import Document, Page, PageStatus

    doc = Document(document_id="d", pages=[Page(page_number=1, status=PageStatus.OK,
                                                width=10, height=10)])
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        index = json.loads(_write_index(doc, Path(tmp)).read_text(encoding="utf-8"))

    assert "elements" not in json.dumps(index["pages"][0]["issues"])
    assert index["pages"][0]["elements"] == 0


def test_bundle_requires_an_output_directory(text_pdf):
    """A directory tree cannot go to stdout, and writing one into the user's
    cwd unasked is not a reasonable default for a tool run in a loop."""
    with pytest.raises(SystemExit):
        main([str(text_pdf), "-f", "bundle", "-q"])


def test_layout_and_table_flags_exist():
    """These features shipped reachable only from the Python API: tables and
    layout regions were implemented, tested, and impossible to run from the
    command line, which is the only way the tool is actually used."""
    import argparse
    import contextlib
    import io

    help_text = io.StringIO()
    with contextlib.redirect_stdout(help_text), pytest.raises(SystemExit):
        main(["--help"])

    assert "--tables" in help_text.getvalue()
    assert "--layout" in help_text.getvalue()


def test_the_model_flags_do_not_break_a_run_without_them(text_pdf, tmp_path):
    """Default stays stub-detectors and four core dependencies."""
    assert main([str(text_pdf), "-o", str(tmp_path / "a.json"), "-q"]) == 0


# --- the --ocr flag ---------------------------------------------------------


def test_a_pdf_passed_as_the_language_names_the_right_mistake():
    """`docyx --ocr scan.pdf` swallows the PDF as the language, and argparse
    then reports the PDF as missing — blaming the argument you did supply."""
    from docyx.cli import ocr_language

    with pytest.raises(argparse.ArgumentTypeError) as exc:
        ocr_language("scan.pdf")

    assert "looks like a file" in str(exc.value)

    with pytest.raises(argparse.ArgumentTypeError):
        ocr_language("some/dir/scan.PDF")


def test_a_real_language_code_passes_through():
    from docyx.cli import ocr_language

    assert ocr_language("ben+eng") == "ben+eng"
    assert ocr_language("ara") == "ara"


def test_an_uninstalled_language_is_refused_before_any_page_runs(tmp_path, capsys):
    """A missing 5 MB data file otherwise fails every page as STAGE_FAILED, so
    the summary reads `1 failed` and the user concludes the scan is unreadable.
    """
    pytest.importorskip("pytesseract")
    from docyx.analysis.detectors.tesseract import TesseractDetector

    try:
        installed = set(TesseractDetector().languages())
    except ImportError:
        pytest.skip("tesseract is not installed")

    absent = "zzz"
    assert absent not in installed

    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "hello", fontsize=11)
    path = tmp_path / "doc.pdf"
    doc.save(str(path))
    doc.close()

    with pytest.raises(SystemExit) as exc:
        main([str(path), "--ocr", absent, "-o", str(tmp_path / "out.json")])

    assert exc.value.code == 2
    message = capsys.readouterr().err
    assert absent in message
    # Naming what IS available is the difference between a dead end and a fix.
    assert "Installed:" in message


def test_a_percentage_is_refused_where_a_probability_belongs():
    """`--ocr-min-confidence 50` reads as "50%" and float() accepts it, then
    drops every recognised line -- so a scan comes back `failed` with
    NO_TEXT_LAYER and looks unreadable rather than over-filtered."""
    from docyx.cli import ocr_confidence

    assert ocr_confidence("0.4") == 0.4
    assert ocr_confidence("0") == 0.0
    assert ocr_confidence("1") == 1.0

    for bad in ("50", "-1", "1.5"):
        with pytest.raises(argparse.ArgumentTypeError) as exc:
            ocr_confidence(bad)
        assert "between 0 and 1" in str(exc.value)

    with pytest.raises(argparse.ArgumentTypeError):
        ocr_confidence("abc")


def test_a_missing_output_directory_is_created_not_a_traceback(tmp_path):
    """Batch mode already creates its output directory, so a single file whose
    parent is missing must not be the one path that raises a bare
    FileNotFoundError out of write_text()."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "hello", fontsize=11)
    source = tmp_path / "in.pdf"
    doc.save(str(source))
    doc.close()

    target = tmp_path / "does" / "not" / "exist" / "out.json"
    assert main([str(source), "-o", str(target)]) == 0
    assert target.exists()


def test_every_tree_format_creates_its_parent_too(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "hello", fontsize=11)
    source = tmp_path / "in.pdf"
    doc.save(str(source))
    doc.close()

    for fmt in ("bundle", "blocks"):
        target = tmp_path / fmt / "nested" / "out"
        assert main([str(source), "-f", fmt, "-o", str(target)]) == 0
        assert any(target.iterdir())


def test_a_document_level_issue_reaches_the_summary(tmp_path, capsys):
    """A warning nobody reads the JSON for is invisible, which defeats the
    point of warning rather than blocking."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "restricted", fontsize=11)
    source = tmp_path / "no_copy.pdf"
    doc.save(str(source), encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner",
             permissions=fitz.PDF_PERM_ACCESSIBILITY)
    doc.close()

    assert main([str(source), "-o", str(tmp_path / "out.json")]) == 0
    assert "EXTRACTION_NOT_PERMITTED" in capsys.readouterr().err
