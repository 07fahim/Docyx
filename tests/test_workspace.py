"""The local workspace server.

Pinned as a contract between the server and the page that consumes it: the
JavaScript reads specific fields, and a schema change that drops one would
otherwise only show up as a blank screen.
"""

import json
import threading
import time
import urllib.request

import fitz
import pytest

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


@pytest.fixture
def base_url(pdf):
    port = 8791
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

    after = post(f"{base_url}/api/edit?page=0", {"id": before["id"], "text": "corrected"})

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
    second = post(f"{base_url}/api/edit?page=1", {"id": element_id, "text": "second"})

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
