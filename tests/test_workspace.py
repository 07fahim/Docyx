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
