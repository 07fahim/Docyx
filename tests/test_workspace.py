"""The local workspace server.

Pinned as a contract between the server and the page that consumes it: the
JavaScript reads specific fields, and a schema change that drops one would
otherwise only show up as a blank screen.
"""

import itertools
import json
import threading
import time
import urllib.request

import fitz
import pytest

from pydantic import ValidationError

from docyx.core.geometry import BoundingBox
from docyx.schema.models import ASSIGNABLE_TYPES
from docyx.workspace.server import Workspace, serve


@pytest.fixture
def pdf(tmp_path):
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 100), f"Page {i} text.", fontsize=11)
    path = tmp_path / "doc.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


#: A port per test. A fixed one meant only the first `serve` ever bound it and
#: every later test silently talked to the first test's Workspace — harmless
#: while the tests were read-only, wrong the moment one of them edits.
_ports = itertools.count(8791)


@pytest.fixture
def base_url(pdf):
    port = next(_ports)
    threading.Thread(
        target=serve, args=(pdf,),
        kwargs={"port": port, "open_browser": False}, daemon=True,
    ).start()
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    return f"http://127.0.0.1:{port}"


def get(url):
    return urllib.request.urlopen(url, timeout=5).read()


def test_extraction_is_cached_per_page(pdf):
    """The viewer re-requests a page on every navigation; re-running layout or
    OCR each time would make paging cost seconds."""
    workspace = Workspace(pdf)
    first = workspace.page(0)

    assert workspace.page(0) is first
    workspace.close()


def test_the_page_endpoint_carries_every_field_the_viewer_reads(base_url):
    data = json.loads(get(f"{base_url}/api/page?page=0"))

    assert {"filename", "page_count", "index", "page"} <= set(data)
    page = data["page"]
    assert {"status", "source_type", "elements", "warnings", "errors"} <= set(page)

    element = page["elements"][0]
    assert {"x", "y", "width", "height"} == set(element["geometry"]["bbox"])
    assert element["confidence"]["type"] in {"exact", "detected", "inferred"}
    assert "modified_by_user" in element["provenance"]


def test_the_image_endpoint_returns_a_png(base_url):
    assert get(f"{base_url}/api/image?page=0")[:4] == b"\x89PNG"


def test_an_out_of_range_page_is_clamped_not_404(base_url):
    """The viewer's next/prev buttons are the only caller; an off-by-one there
    should not blank the screen."""
    assert json.loads(get(f"{base_url}/api/page?page=999"))["index"] == 2
    assert json.loads(get(f"{base_url}/api/page?page=-5"))["index"] == 0


def test_an_unknown_path_is_a_404(base_url):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(f"{base_url}/nope")

    assert exc.value.code == 404


def post(url, payload):
    """Mutating routes answer {element, history}."""
    request = urllib.request.Request(
        url, json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(request, timeout=5).read())


def first_element_id(base_url, page=0):
    return json.loads(get(f"{base_url}/api/page?page={page}"))["page"]["elements"][0]["id"]


def test_an_edit_records_provenance_rather_than_overwriting_it(base_url):
    """`source` is a fact about history and must survive a correction: a fixed
    OCR line has to stay distinguishable from one a human drew on a blank scan.
    """
    before = json.loads(get(f"{base_url}/api/page?page=0"))["page"]["elements"][0]

    after = post(f"{base_url}/api/edit?page=0",
                 {"id": before["id"], "text": "corrected"})["element"]

    assert after["text"] == "corrected"
    assert after["provenance"]["source"] == before["provenance"]["source"] == "native_pdf"
    assert after["provenance"]["modified_by_user"] is True
    assert after["provenance"]["original_text"] == before["text"]
    # A person reading the rendered page outranks any extractor.
    assert (after["confidence"]["value"], after["confidence"]["type"]) == (1.0, "exact")


def test_editing_twice_over_the_wire_keeps_the_original_not_the_first_fix(base_url):
    """The same invariant `edit_text` pins, asserted through the HTTP path that
    the workspace actually uses."""
    element_id = first_element_id(base_url, page=1)
    original = json.loads(get(f"{base_url}/api/page?page=1"))["page"]["elements"][0]["text"]

    post(f"{base_url}/api/edit?page=1", {"id": element_id, "text": "first"})
    second = post(f"{base_url}/api/edit?page=1",
                  {"id": element_id, "text": "second"})["element"]

    assert second["provenance"]["original_text"] == original


def test_an_edit_survives_re_requesting_the_page(base_url):
    """The per-page cache is the session's working copy, so navigating away and
    back must not silently discard a correction."""
    element_id = first_element_id(base_url, page=2)

    post(f"{base_url}/api/edit?page=2", {"id": element_id, "text": "persisted"})

    reloaded = json.loads(get(f"{base_url}/api/page?page=2"))["page"]["elements"][0]
    assert reloaded["text"] == "persisted"


def test_editing_an_unknown_element_is_a_404_not_a_500(base_url):
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"{base_url}/api/edit?page=0", {"id": "no_such_element", "text": "x"})

    assert exc.value.code == 404


def test_an_oversized_edit_is_refused(base_url):
    """A corrected line, not a document."""
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"{base_url}/api/edit?page=0",
             {"id": first_element_id(base_url), "text": "x" * 70_000})

    assert exc.value.code == 413


def test_a_child_element_is_reachable_by_id(tmp_path):
    """Cells and style runs live in `children`; a flat scan of `elements` would
    make them uneditable and the failure would look like a missing element."""
    doc = fitz.open()
    page = doc.new_page()
    where = page.insert_text((72, 100), "Note: ", fontsize=11, fontname="hebo")
    page.insert_text((110, 100), "and the rest.", fontsize=11)
    path = tmp_path / "mixed.pdf"
    doc.save(str(path))
    doc.close()

    workspace = Workspace(str(path))
    elements = workspace.page(0).pages[0].elements
    children = [c for e in elements for c in e.children]
    if not children:
        pytest.skip("this fixture produced no style runs")

    edited = workspace.edit(0, children[0].id, "changed")

    assert edited.text == "changed"
    assert edited.provenance.modified_by_user is True
    workspace.close()


# --- geometry --------------------------------------------------------------


def test_moving_an_element_keeps_the_geometry_the_machine_proposed(pdf):
    workspace = Workspace(pdf)
    element = workspace.page(0).pages[0].elements[0]
    was = element.geometry.bbox.model_dump()

    workspace.move(0, element.id, BoundingBox(x=5, y=6, width=70, height=8))

    assert element.geometry.bbox.model_dump() == {"x": 5, "y": 6, "width": 70, "height": 8}
    assert element.provenance.original_geometry["bbox"] == was
    assert element.provenance.modified_by_user is True
    workspace.close()


def test_a_text_edit_never_overwrites_a_geometry_original(pdf):
    """The two originals are guarded separately. Sharing one `modified_by_user`
    flag as the guard meant whichever edit came second recorded nothing."""
    workspace = Workspace(pdf)
    element = workspace.page(0).pages[0].elements[0]
    text_was, box_was = element.text, element.geometry.bbox.model_dump()

    workspace.move(0, element.id, BoundingBox(x=1, y=1, width=1, height=1))
    workspace.edit(0, element.id, "later")

    assert element.provenance.original_geometry["bbox"] == box_was
    assert element.provenance.original_text == text_was
    workspace.close()


# --- undo / redo -----------------------------------------------------------


def test_undo_restores_provenance_not_just_the_value(base_url):
    """Undo cannot be "edit it back": that leaves modified_by_user set and the
    element exact/1.0, claiming a human vouched for a value they took back."""
    before = json.loads(get(f"{base_url}/api/page?page=0"))["page"]["elements"][0]

    post(f"{base_url}/api/edit?page=0", {"id": before["id"], "text": "wrong"})
    undone = post(f"{base_url}/api/undo?page=0", {})["element"]

    assert undone["text"] == before["text"]
    assert undone["provenance"]["modified_by_user"] is False
    assert "original_text" not in undone["provenance"]
    assert undone["confidence"] == before["confidence"]


def test_redo_reapplies_and_a_new_edit_forks_the_timeline(base_url):
    element_id = first_element_id(base_url, page=1)

    post(f"{base_url}/api/edit?page=1", {"id": element_id, "text": "one"})
    post(f"{base_url}/api/undo?page=1", {})
    redone = post(f"{base_url}/api/redo?page=1", {})

    assert redone["element"]["text"] == "one"
    assert redone["history"] == {"undo": 1, "redo": 0}

    post(f"{base_url}/api/undo?page=1", {})
    forked = post(f"{base_url}/api/edit?page=1", {"id": element_id, "text": "other"})
    assert forked["history"]["redo"] == 0, "a fresh edit must discard the redos"


def test_history_is_per_page(base_url):
    """A single global stack would make Ctrl+Z reach into a page already left."""
    post(f"{base_url}/api/edit?page=0", {"id": first_element_id(base_url, 0), "text": "a"})

    assert json.loads(get(f"{base_url}/api/page?page=2"))["history"]["undo"] == 0
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"{base_url}/api/undo?page=2", {})
    assert exc.value.code == 409


def test_undoing_an_empty_history_is_409_not_500(base_url):
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"{base_url}/api/redo?page=1", {})

    assert exc.value.code == 409


# --- export ----------------------------------------------------------------


def test_export_writes_every_page_including_unvisited_ones(base_url, tmp_path, pdf):
    """Editing page 0 must not export a one-page document."""
    target = tmp_path / "out.json"
    post(f"{base_url}/api/edit?page=0", {"id": first_element_id(base_url), "text": "kept"})

    post(f"{base_url}/api/export", {"path": str(target)})

    exported = json.loads(target.read_text(encoding="utf-8"))
    assert len(exported["pages"]) == 3
    assert exported["page_count"] == 3
    assert exported["pages"][0]["elements"][0]["text"] == "kept"
    assert exported["schema_version"] == "1.8"


def test_export_defaults_beside_the_pdf(pdf):
    workspace = Workspace(pdf)

    path = workspace.export()

    assert path.name == "doc.docyx.json"
    assert json.loads(path.read_text(encoding="utf-8"))["pages"]
    workspace.close()


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_export_validates_before_writing(pdf, tmp_path):
    """A file that fails its own schema must not exist on disk."""
    workspace = Workspace(pdf)
    target = tmp_path / "invalid.json"
    # Pydantic does not validate on assignment, so a bad value reaches export.
    workspace.page(0).pages[0].elements[0].confidence.value = "not a number"

    with pytest.raises(ValidationError):
        workspace.export(str(target))

    assert not target.exists()
    workspace.close()


def test_a_failed_page_does_not_block_export(tmp_path):
    """Partial results are the documented contract; refusing to export good
    pages over one bad one would invert it."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "readable", fontsize=11)
    doc.new_page()  # no text layer -> fails the gate
    path = tmp_path / "mixed.pdf"
    doc.save(str(path))
    doc.close()

    workspace = Workspace(str(path))
    exported = json.loads(workspace.export().read_text(encoding="utf-8"))

    assert [p["status"] for p in exported["pages"]] == ["ok", "failed"]
    workspace.close()


# --- resume ----------------------------------------------------------------


def edited_export(pdf, tmp_path, text="corrected"):
    """Edit one line, export, and hand back a fresh Workspace over the same PDF."""
    first = Workspace(pdf)
    element = first.page(0).pages[0].elements[0]
    machine_said = element.text
    first.edit(0, element.id, text)
    path = first.export(str(tmp_path / "saved.json"))
    first.close()
    return Workspace(pdf), element.id, machine_said, path


def test_a_saved_edit_is_reattached_when_the_extraction_still_agrees(pdf, tmp_path):
    fresh, element_id, machine_said, path = edited_export(pdf, tmp_path)

    report = fresh.restore(str(path))

    assert (report["applied"], report["conflicts"], report["orphans"]) == (1, [], [])
    # Idempotent: without this, a second import reports every edit as a
    # conflict, because the target no longer matches its own original_text.
    again = fresh.restore(str(path))
    assert (again["applied"], again["unchanged"], again["conflicts"]) == (0, 1, [])
    restored = fresh.find(0, element_id)
    assert restored.text == "corrected"
    assert restored.provenance.original_text == machine_said
    assert restored.provenance.modified_by_user is True
    fresh.close()


def test_a_changed_line_is_a_conflict_and_is_never_applied(pdf, tmp_path):
    """The measured failure: an id survives a code change while naming
    different content. Reattaching on the id alone moves a human correction
    onto a line that changed underneath it, with nothing to show for it."""
    fresh, element_id, machine_said, path = edited_export(pdf, tmp_path)
    # Stand in for an extraction change: the machine now says something else.
    fresh.find(0, element_id).text = "the extractor changed its mind"

    report = fresh.restore(str(path))

    assert report["applied"] == 0
    assert report["conflicts"][0]["id"] == element_id
    assert report["conflicts"][0]["then"] == machine_said
    assert fresh.find(0, element_id).text == "the extractor changed its mind"
    assert fresh.find(0, element_id).provenance.modified_by_user is False
    fresh.close()


def test_a_vanished_id_is_reported_orphaned_not_silently_dropped(pdf, tmp_path):
    fresh, element_id, _, path = edited_export(pdf, tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["pages"][0]["elements"][0]["id"] = "page1_b99_l99"
    path.write_text(json.dumps(saved), encoding="utf-8")

    report = fresh.restore(str(path))

    assert report["applied"] == 0
    assert report["orphans"] == [{"page": 0, "id": "page1_b99_l99"}]
    fresh.close()


def test_a_moved_box_round_trips_through_its_original_geometry(pdf, tmp_path):
    first = Workspace(pdf)
    element = first.page(0).pages[0].elements[0]
    machine_box = element.geometry.bbox.model_dump()
    first.move(0, element.id, BoundingBox(x=11, y=22, width=33, height=44))
    path = first.export(str(tmp_path / "moved.json"))
    first.close()

    fresh = Workspace(pdf)
    report = fresh.restore(str(path))

    assert report["applied"] == 1
    restored = fresh.find(0, element.id)
    assert restored.geometry.bbox.model_dump() == {"x": 11, "y": 22, "width": 33, "height": 44}
    assert restored.provenance.original_geometry["bbox"] == machine_box
    fresh.close()


def test_restoring_is_undoable(pdf, tmp_path):
    """Reattach goes through `edit`/`move`, so an unwanted import is one
    Ctrl+Z away rather than a thing you live with."""
    fresh, element_id, machine_said, path = edited_export(pdf, tmp_path)

    fresh.restore(str(path))
    fresh.undo(0)

    assert fresh.find(0, element_id).text == machine_said
    assert fresh.find(0, element_id).provenance.modified_by_user is False
    fresh.close()


def test_untouched_elements_are_not_reattached(pdf, tmp_path):
    """Only `modified_by_user` elements are carried over; re-applying every
    element would rewrite the whole page from a stale file."""
    fresh, _, _, path = edited_export(pdf, tmp_path)

    report = fresh.restore(str(path))

    assert report["applied"] == 1, "the export holds many elements, one of them edited"
    fresh.close()


def test_importing_with_no_saved_file_is_a_404(base_url):
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"{base_url}/api/import", {"path": "no_such_file.json"})

    assert exc.value.code == 404


# --- category ---------------------------------------------------------------


def test_assignable_types_cover_the_orderable_roles():
    """`ASSIGNABLE_TYPES` is written out in models.py because reading_order
    imports that module. This is what stops the two drifting."""
    from docyx.analysis.reading_order import TEXT_ROLES

    assert TEXT_ROLES <= ASSIGNABLE_TYPES
    assert {"table", "figure"} <= ASSIGNABLE_TYPES
    # Containers are deliberately absent: a person is not reassigning those.
    assert not {"text_region", "table_cell", "text_span", "rule"} & ASSIGNABLE_TYPES


def test_retyping_keeps_what_the_machine_called_it(pdf):
    """Without a layout model every element is `text`, so a title, a heading
    and a paragraph are indistinguishable until a person says otherwise."""
    workspace = Workspace(pdf)
    element = workspace.page(0).pages[0].elements[0]
    assert element.type == "text", "the default really is flat"

    workspace.retype(0, element.id, "section_header")

    assert element.type == "section_header"
    assert element.provenance.original_type == "text"
    assert element.provenance.modified_by_user is True
    assert element.confidence.type.value == "exact"
    workspace.close()


def test_retyping_twice_keeps_the_original_not_the_first_choice(pdf):
    workspace = Workspace(pdf)
    element = workspace.page(0).pages[0].elements[0]

    workspace.retype(0, element.id, "title")
    workspace.retype(0, element.id, "section_header")

    assert element.provenance.original_type == "text"
    workspace.close()


def test_a_type_edit_never_overwrites_the_other_originals(pdf):
    """Three claims, three separate guards. Sharing one meant whichever edit
    came last recorded nothing."""
    workspace = Workspace(pdf)
    element = workspace.page(0).pages[0].elements[0]
    was_text, was_box = element.text, element.geometry.bbox.model_dump()

    workspace.retype(0, element.id, "title")
    workspace.edit(0, element.id, "corrected")
    workspace.move(0, element.id, BoundingBox(x=1, y=1, width=9, height=9))

    assert element.provenance.original_type == "text"
    assert element.provenance.original_text == was_text
    assert element.provenance.original_geometry["bbox"] == was_box
    workspace.close()


def test_an_unknown_type_is_refused(pdf):
    """`type` is a bare string, so an unchecked value would become a silent
    new element type that nothing downstream handles."""
    workspace = Workspace(pdf)
    element = workspace.page(0).pages[0].elements[0]

    with pytest.raises(ValueError):
        workspace.retype(0, element.id, "Section-header")  # the export's casing

    assert element.type == "text"
    workspace.close()


def test_undo_restores_the_type_too(base_url):
    before = json.loads(get(f"{base_url}/api/page?page=0"))["page"]["elements"][0]

    post(f"{base_url}/api/retype?page=0", {"id": before["id"], "type": "title"})
    undone = post(f"{base_url}/api/undo?page=0", {})["element"]

    assert undone["type"] == before["type"]
    assert undone["provenance"]["modified_by_user"] is False
    assert "original_type" not in undone["provenance"]


def test_a_retype_round_trips_through_export_and_import(pdf, tmp_path):
    first = Workspace(pdf)
    element = first.page(0).pages[0].elements[0]
    first.retype(0, element.id, "section_header")
    path = first.export(str(tmp_path / "typed.json"))
    first.close()

    fresh = Workspace(pdf)
    report = fresh.restore(str(path))

    assert report["applied"] == 1
    restored = fresh.find(0, element.id)
    assert restored.type == "section_header"
    assert restored.provenance.original_type == "text"
    fresh.close()


def test_a_changed_type_is_a_conflict(pdf, tmp_path):
    first = Workspace(pdf)
    element = first.page(0).pages[0].elements[0]
    first.retype(0, element.id, "title")
    path = first.export(str(tmp_path / "typed.json"))
    first.close()

    fresh = Workspace(pdf)
    fresh.find(0, element.id).type = "caption"  # stand in for an extraction change
    report = fresh.restore(str(path))

    assert report["applied"] == 0
    assert report["conflicts"][0]["field"] == "type"
    assert fresh.find(0, element.id).type == "caption"
    fresh.close()


def test_the_vocabulary_travels_with_the_page(base_url):
    """The viewer builds its control from this, so it cannot offer a type the
    server would refuse."""
    types = json.loads(get(f"{base_url}/api/page?page=0"))["types"]

    assert set(types) == ASSIGNABLE_TYPES


# --- heading suggestions ----------------------------------------------------


def test_suggestions_are_proposed_never_applied(pdf):
    """The machine proposes and a person accepts. Writing `type` here would
    force a lie about confidence: the text was read exactly even when the
    category is a guess."""
    workspace = Workspace(pdf)
    before = [el.type for el in workspace.page(0).pages[0].elements]

    workspace.suggest_headings(0)

    assert [el.type for el in workspace.page(0).pages[0].elements] == before
    workspace.close()


def test_a_page_with_no_dominant_body_size_proposes_nothing(tmp_path):
    """A form has no body size, so "bigger than body" stops meaning "heading".
    Measured: irs_fw9 p0 proposed 15 of 133 lines, mostly wrong."""
    from docyx.analysis.headings import body_size

    doc = fitz.open()
    page = doc.new_page()
    for i, size in enumerate([7, 9, 11, 13, 15, 17, 19, 21, 23, 25]):
        page.insert_text((72, 80 + i * 40), f"line {i}", fontsize=size)
    path = tmp_path / "form.pdf"
    doc.save(str(path))
    doc.close()

    workspace = Workspace(str(path))
    assert body_size(workspace.page(0).pages[0].elements) is None
    assert workspace.suggest_headings(0) == []
    workspace.close()


def test_a_heading_is_proposed_on_prose(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "A Much Bigger Heading", fontsize=20)
    for i in range(12):
        page.insert_text((72, 120 + i * 20), f"body line {i} of ordinary prose", fontsize=10)
    path = tmp_path / "prose.pdf"
    doc.save(str(path))
    doc.close()

    workspace = Workspace(str(path))
    suggestions = workspace.suggest_headings(0)

    assert len(suggestions) == 1
    assert suggestions[0]["type"] == "title"
    assert workspace.find(0, suggestions[0]["id"]).text.startswith("A Much Bigger")
    workspace.close()


def test_two_lines_at_the_largest_size_are_headings_not_two_titles(tmp_path):
    """A page has at most one title. `wiki_ar.pdf` p6 has two same-sized
    section headings and no title at all."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "First Section", fontsize=16)
    page.insert_text((72, 300), "Second Section", fontsize=16)
    for i in range(12):
        page.insert_text((72, 120 + i * 12), f"body line {i}", fontsize=10)
    path = tmp_path / "two.pdf"
    doc.save(str(path))
    doc.close()

    workspace = Workspace(str(path))
    assert {s["type"] for s in workspace.suggest_headings(0)} == {"section_header"}
    workspace.close()


def test_the_biggest_line_on_an_interior_page_is_not_a_title(tmp_path):
    """A document has one title and it is on page one.

    `nasa_budget.pdf` p88 sets its program name larger than anything else on
    the page, and proposing `title` there was wrong on every interior page in
    the corpus. Found by scripts/measure_types.py, not by a reader.
    """
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((72, 80), "Program Name In Large Type", fontsize=20)
        for i in range(12):
            page.insert_text((72, 120 + i * 20), f"body line {i} of ordinary prose", fontsize=10)
    path = tmp_path / "interior.pdf"
    doc.save(str(path))
    doc.close()

    workspace = Workspace(str(path))
    assert [s["type"] for s in workspace.suggest_headings(0)] == ["title"]
    assert [s["type"] for s in workspace.suggest_headings(1)] == ["section_header"]
    workspace.close()


def test_a_suggestion_does_not_overwrite_a_human_or_a_model(tmp_path):
    """Both outrank a font-size guess."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "A Much Bigger Heading", fontsize=20)
    for i in range(12):
        page.insert_text((72, 120 + i * 20), f"body line {i} of ordinary prose", fontsize=10)
    path = tmp_path / "prose.pdf"
    doc.save(str(path))
    doc.close()

    workspace = Workspace(str(path))
    heading_id = workspace.suggest_headings(0)[0]["id"]
    workspace.retype(0, heading_id, "caption")

    assert workspace.suggest_headings(0) == [], "a human's answer is not re-proposed"
    workspace.close()


# --- page check -------------------------------------------------------------


def test_check_reports_a_defect_a_human_edit_created(pdf):
    """The report exists because these defects validate and export cleanly.

    Dragging a box to nothing is the easiest way to make one, and the check
    must see it after the edit rather than before.
    """
    workspace = Workspace(pdf)
    element = workspace.page(0).pages[0].elements[0]
    assert workspace.check(0) == []

    workspace.move(0, element.id, BoundingBox(x=10, y=10, width=0, height=0))
    findings = workspace.check(0)

    assert [f["code"] for f in findings] == ["ZERO_SIZE_BOX"]
    assert findings[0]["ids"] == [element.id]
    workspace.close()


def test_check_is_served_and_never_mutates(base_url):
    before = json.loads(get(f"{base_url}/api/page?page=0"))
    assert json.loads(get(f"{base_url}/api/check?page=0")) == {"findings": []}
    after = json.loads(get(f"{base_url}/api/page?page=0"))

    assert before["page"] == after["page"]
