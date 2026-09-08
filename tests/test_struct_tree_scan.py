"""Tests for the tagged-PDF prevalence instrument (scripts/measure_struct_tree.py).

The measurement decides whether the §10 struct-tree path earns a phase, so the
detection has to be trustworthy — a false negative would wrongly kill the idea.
"""

import sys
from pathlib import Path

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from measure_struct_tree import HIGH_VALUE_TYPES, inspect  # noqa: E402


def _tagged_pdf(path: Path, types=("/H1", "/P", "/Table", "/Figure"), lang="(en-US)"):
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "Title")
    catalog = doc.pdf_catalog()

    kids = []
    for s in types:
        x = doc.get_new_xref()
        doc.update_object(x, f"<< /Type /StructElem /S {s} >>")
        kids.append(x)

    parent = doc.get_new_xref()
    doc.update_object(
        parent,
        "<< /Type /StructElem /S /Document /K [%s] >>" % " ".join(f"{k} 0 R" for k in kids),
    )
    root = doc.get_new_xref()
    doc.update_object(root, f"<< /Type /StructTreeRoot /K {parent} 0 R >>")
    doc.xref_set_key(catalog, "StructTreeRoot", f"{root} 0 R")
    if lang:
        doc.xref_set_key(catalog, "Lang", lang)

    mark = doc.get_new_xref()
    doc.update_object(mark, "<< /Marked true >>")
    doc.xref_set_key(catalog, "MarkInfo", f"{mark} 0 R")

    doc.save(str(path))
    doc.close()
    return path


def _plain_pdf(path: Path):
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "plain text")
    doc.save(str(path))
    doc.close()
    return path


def test_detects_a_tagged_document(tmp_path):
    result = inspect(_tagged_pdf(tmp_path / "tagged.pdf"))

    assert result["error"] is None
    assert result["tagged"] is True
    assert result["marked"] is True
    assert result["lang"] == "en-US"
    assert result["usable"] is True
    assert set(result["high_value_types"]) == {"H1", "Table", "Figure"}
    assert result["struct_types"]["Document"] == 1


def test_untagged_document_is_not_a_false_positive(tmp_path):
    result = inspect(_plain_pdf(tmp_path / "plain.pdf"))

    assert result["error"] is None
    assert result["tagged"] is False
    assert result["usable"] is False
    assert result["struct_types"] == {}
    assert result["lang"] is None


def test_tagged_but_structurally_useless_is_reported_as_unusable(tmp_path):
    """A tree of only /Document and /P buys nothing the vision path lacks —
    counting it as a win would overstate the case for the struct-tree phase."""
    path = _tagged_pdf(tmp_path / "thin.pdf", types=("/P", "/P", "/Span"), lang=None)
    result = inspect(path)

    assert result["tagged"] is True
    assert result["usable"] is False
    assert result["high_value_types"] == []


def test_unreadable_file_is_reported_not_raised(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")

    result = inspect(broken)

    assert result["error"] is not None
    assert result["tagged"] is False


def test_high_value_types_cover_the_things_the_plan_wants():
    """§10 reading order, §11 table structure, §9 figures."""
    assert {"H1", "H2", "Table", "TR", "TH", "TD", "Figure", "Caption"} <= HIGH_VALUE_TYPES
