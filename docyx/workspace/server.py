"""A local workspace for looking at what Docyx extracted.

    python -m docyx.workspace document.pdf

Serves the rendered page with its extracted elements drawn over it, so a
regression is visible in a glance rather than after writing a measurement
script. Three of the twelve findings in the last code review were things no
test caught because no test ever rendered the output.

stdlib http.server, deliberately: this needs to serve one image, one JSON blob
and one HTML file, and adding a web framework for that would put a fifth
dependency in the core install. Swap in FastAPI when uploads, auth or
concurrency arrive — the routes are thin on purpose.
"""

import json
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from docyx.analysis.check import check_page
from docyx.analysis.headings import suggest
from docyx.core.geometry import BoundingBox
from docyx.core.metadata import ProvenanceSource
from docyx.pdf.renderer import PDFRenderer
from docyx.pipeline.extractor import DocyxPipeline
from docyx.schema.models import ASSIGNABLE_TYPES, Document, Element, Page

STATIC = Path(__file__).parent / "static"

#: A corrected line, not a document. Anything larger is a client bug.
MAX_EDIT_BYTES = 64 * 1024


def _walk(page: Page):
    """Every element on the page, children included."""
    stack = list(page.elements) + list(page.diagnostic_elements)
    while stack:
        element = stack.pop()
        yield element
        stack.extend(element.children)


def _walk_json(page: Dict[str, Any]):
    """`_walk` over an exported page, which is plain dicts."""
    stack = list(page.get("elements") or []) + list(page.get("diagnostic_elements") or [])
    while stack:
        element = stack.pop()
        yield element
        stack.extend(element.get("children") or [])


def _same_box(a: Dict[str, float], b: Dict[str, float]) -> bool:
    # Tolerant, because these are floats that travelled through JSON.
    return all(abs(a[k] - b[k]) < 1e-6 for k in ("x", "y", "width", "height"))


def _snapshot(element: Element) -> Dict[str, Any]:
    """Everything an edit can touch, deep-copied.

    Undo restores this wholesale rather than re-applying `edit_text` in
    reverse: re-editing would leave `modified_by_user` set and the element
    `exact`/1.0, claiming a human vouched for a value they just took back.
    """
    return {
        "text": element.text,
        "type": element.type,
        "geometry": element.geometry.model_copy(deep=True),
        "confidence": element.confidence.model_copy(deep=True),
        "provenance": element.provenance.model_copy(deep=True),
    }


def _restore(element: Element, state: Dict[str, Any]) -> Element:
    element.text = state["text"]
    element.type = state["type"]
    element.geometry = state["geometry"]
    element.confidence = state["confidence"]
    element.provenance = state["provenance"]
    return element


class Workspace:
    """Holds the open document and renders pages on demand.

    Extraction is cached per page: the viewer re-requests a page whenever the
    user navigates back to it, and re-running layout or OCR each time would
    make navigation cost seconds.
    """

    def __init__(self, pdf_path: str, pipeline: Optional[DocyxPipeline] = None):
        self.pdf_path = pdf_path
        self.pipeline = pipeline or DocyxPipeline()
        self.renderer = PDFRenderer(pdf_path)
        self.page_count = self.renderer.page_count()
        self._pages: dict = {}
        self._undo: Dict[int, List] = {}
        self._redo: Dict[int, List] = {}
        # The server is threaded and this cache is the session's working copy,
        # so populating it is a read-modify-write that two requests can race.
        # Reentrant because every mutator calls find() -> page() beneath itself.
        #
        # ponytail: one lock for the whole Workspace. Extraction is seconds with
        # a model, so if page loads ever need to overlap, make it per index --
        # but the correctness argument below is what the lock is really for.
        self._lock = threading.RLock()

    def page(self, index: int) -> Document:
        """Extract a page, or return the cached copy.

        Locked, and not merely to avoid duplicate work. Without it, two
        concurrent requests for an UNCACHED page each build their own
        `Document`; one wins the dict slot and the other is discarded -- so an
        edit applied through the loser was accepted, answered 200 with the
        correction, and silently never reached the page. Reproduced 6 times out
        of 6 before this lock existed.
        """
        with self._lock:
            if index not in self._pages:
                self._pages[index] = self.pipeline.process(
                    self.pdf_path, Path(self.pdf_path).stem, pages=[index]
                )
            return self._pages[index]

    def image(self, index: int) -> bytes:
        return self.renderer.render_page(index)

    def find(self, index: int, element_id: str) -> Element:
        for element in _walk(self.page(index).pages[0]):
            if element.id == element_id:
                return element
        raise KeyError(element_id)

    def edit(self, index: int, element_id: str, text: str) -> Element:
        """Apply a human correction through `edit_text`, never by assignment.

        The edit lands on the cached `Document`, so it survives navigating
        away and back — the cache is the session's working copy.
        """
        with self._lock:
            return self._record(index, self.find(index, element_id)).edit_text(text)

    def move(self, index: int, element_id: str, bbox: BoundingBox) -> Element:
        """Correct an element's position or size, and re-read its text (§10).

        **Moving a box without re-reading leaves the element asserting two
        things that disagree**: a region, and a string that came from a
        different region. Grow a line box to take in the word the extractor
        clipped and the whole point of the correction is that the word joins
        the text — which it did not, until this re-read existed.

        Both mutations sit under one `_record`, so the gesture is one undo
        rather than two: a person dragged a handle once.

        Native text only. An OCR line's text is not in any text layer, so
        re-reading would silently blank it; recognising the new crop is the
        real answer there and it needs the detector, not this method.
        """
        with self._lock:
            element = self._record(index, self.find(index, element_id))
            element.edit_geometry(bbox)
            if (element.text is not None
                    and element.provenance.source == ProvenanceSource.NATIVE_PDF):
                # `edit_text`, never assignment, so `original_text` still records
                # the machine's own claim from before the drag.
                element.edit_text(self.renderer.text_extractor().text_in(index, bbox))
                # The style runs described the old box and now describe nothing.
                # Keeping them would leave a line whose children contradict it.
                element.children = [c for c in element.children
                                    if c.type != "text_span"]
            return element

    def retype(self, index: int, element_id: str, type_: str) -> Element:
        """Correct the block's category through `edit_type` (§8)."""
        with self._lock:
            return self._record(index, self.find(index, element_id)).edit_type(type_)

    def suggest_headings(self, index: int) -> List[Dict[str, str]]:
        """Propose heading categories from font size, without applying them.

        A suggestion a person accepts is applied through `retype`, so the
        record says a human decided — which is true, and avoids claiming a
        font-size guess is as good as reading the text.
        """
        return [{"id": i, "type": t}
                for i, t in suggest(self.page(index).pages[0].elements)]

    # --- undo/redo -------------------------------------------------------
    # Per page, because a page is the unit a person works on and a single
    # global stack would make Ctrl+Z reach back into a page they have left.

    def _record(self, index: int, element: Element) -> Element:
        self._undo.setdefault(index, []).append((element, _snapshot(element)))
        # A fresh edit forks the timeline; the abandoned redos are unreachable.
        self._redo.pop(index, None)
        return element

    def undo(self, index: int) -> Element:
        return self._step(self._undo, self._redo, index)

    def redo(self, index: int) -> Element:
        return self._step(self._redo, self._undo, index)

    def _step(self, source: Dict, sink: Dict, index: int) -> Element:
        with self._lock:
            stack = source.get(index) or []
            if not stack:
                raise IndexError(
                    "nothing to undo" if source is self._undo else "nothing to redo"
                )
            element, state = stack.pop()
            sink.setdefault(index, []).append((element, _snapshot(element)))
            return _restore(element, state)

    def history(self, index: int) -> Dict[str, int]:
        return {"undo": len(self._undo.get(index) or []),
                "redo": len(self._redo.get(index) or [])}

    def edited(self) -> bool:
        """Whether this session has corrections that only exist in memory.

        Cached pages only: extracting the unvisited ones to answer would make
        a document picker cost a full run per document, and a page nobody has
        opened cannot have been edited.
        """
        return any(
            element.provenance.modified_by_user
            for document in self._pages.values()
            for element in _walk(document.pages[0])
        )

    def check(self, index: int) -> List[Dict[str, Any]]:
        """Advisory findings for a page. Never mutates, so it is safe to re-run
        after every edit — which is how a reviewer would want to use it."""
        return [
            {"code": f.code, "message": f.message, "ids": f.ids}
            for f in check_page(self.page(index).pages[0])
        ]

    # --- export ----------------------------------------------------------

    def document(self) -> Document:
        """The whole document, edited where this session touched it.

        Pages never visited are extracted now, so exporting a long PDF after
        editing one page still costs a full run.
        """
        first = self.page(0)
        return Document(
            document_id=first.document_id,
            filename=first.filename,
            page_count=self.page_count,
            pages=[self.page(i).pages[0] for i in range(self.page_count)],
        )

    def export(self, target: Optional[str] = None) -> Path:
        """Validate, then write. A file that fails its own schema must not exist.

        "Errors block export" (§ phase 5) means *schema* errors. A `failed`
        page does not block: partial results are the documented contract, and
        refusing to export 199 good pages over one bad one would invert it.
        """
        document = self.document()
        Document.model_validate(document.model_dump())
        path = Path(target) if target else Path(self.pdf_path).with_suffix(".docyx.json")
        path.write_text(
            document.model_dump_json(indent=2, exclude_none=True), encoding="utf-8"
        )
        return path

    # --- resume ----------------------------------------------------------

    def restore(self, source: Optional[str] = None) -> Dict[str, Any]:
        """Reattach edits from a previous export, verifying each one first.

        Ids are positional, so they survive everything the runtime varies and
        nothing the extraction code does — `scripts/measure_id_stability.py`
        measured one past change orphaning 83% of a page's ids while 29 more
        *survived naming different content*. Matching on id alone would move a
        human correction onto a line that had changed underneath it, silently.

        So the check is `original_*` against what the extractor says **now**:
        that is the machine's own claim at the time of the edit, and if it
        still holds the edit still applies. Three outcomes, and only the first
        one writes anything.
        """
        path = Path(source) if source else Path(self.pdf_path).with_suffix(".docyx.json")
        if not path.exists():
            raise KeyError(f"no saved edits at {path}")

        saved = json.loads(path.read_text(encoding="utf-8"))
        report: Dict[str, Any] = {"source": str(path), "applied": 0,
                                  "unchanged": 0, "conflicts": [], "orphans": []}

        for page in saved.get("pages") or []:
            index = page["page_number"] - 1
            if index >= self.page_count:
                continue
            for saved_element in _walk_json(page):
                provenance = saved_element.get("provenance") or {}
                if not provenance.get("modified_by_user"):
                    continue
                self._reattach(index, saved_element, provenance, report)
        return report

    def _reattach(self, index, saved_element, provenance, report) -> None:
        element_id = saved_element["id"]
        try:
            target = self.find(index, element_id)
        except KeyError:
            report["orphans"].append({"page": index, "id": element_id})
            return

        was_text = provenance.get("original_text")
        was_type = provenance.get("original_type")
        was_box = (provenance.get("original_geometry") or {}).get("bbox")

        # Already carrying this exact edit. Without this, importing the same
        # file twice reports every edit as a conflict — the target no longer
        # matches its own `original_text`, because it is the correction.
        if (target.provenance.modified_by_user
                and target.provenance.original_text == was_text
                and target.provenance.original_type == was_type
                and target.type == saved_element["type"]
                and (target.text or "") == (saved_element.get("text") or "")
                and _same_box(saved_element["geometry"]["bbox"],
                              target.geometry.bbox.model_dump())):
            report["unchanged"] += 1
            return

        if was_text is not None and (target.text or "") != was_text:
            report["conflicts"].append({
                "page": index, "id": element_id, "field": "text",
                "then": was_text, "now": target.text,
            })
            return
        if was_type is not None and target.type != was_type:
            report["conflicts"].append({
                "page": index, "id": element_id, "field": "type",
                "then": was_type, "now": target.type,
            })
            return
        if was_box is not None and not _same_box(was_box, target.geometry.bbox.model_dump()):
            report["conflicts"].append({
                "page": index, "id": element_id, "field": "geometry",
                "then": was_box, "now": target.geometry.bbox.model_dump(),
            })
            return

        # Through `move`/`edit`, so restored edits land on the undo stack and
        # re-record their originals from values just proven identical.
        if was_type is not None:
            self.retype(index, element_id, saved_element["type"])
        if was_box is not None:
            self.move(index, element_id, BoundingBox(**saved_element["geometry"]["bbox"]))
        if was_text is not None:
            self.edit(index, element_id, saved_element.get("text") or "")
        report["applied"] += 1

    def close(self) -> None:
        self.renderer.close()


def _handler(documents: "DocumentSet"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: A003 - quieten the default logging
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _page_index(self, query) -> int:
            workspace = documents.current()
            index = int(parse_qs(query).get("page", ["0"])[0])
            # Clamped rather than 404: the viewer's next/prev buttons are the
            # only caller, and an off-by-one there should not blank the screen.
            return max(0, min(index, workspace.page_count - 1))

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
            workspace = documents.current()
            url = urlparse(self.path)
            try:
                if url.path in ("/", "/index.html"):
                    self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/api/page":
                    index = self._page_index(url.query)
                    document = workspace.page(index)
                    payload = {
                        "filename": document.filename,
                        "page_count": workspace.page_count,
                        "index": index,
                        "history": workspace.history(index),
                        "types": sorted(ASSIGNABLE_TYPES),
                        "page": json.loads(
                            document.pages[0].model_dump_json(exclude_none=True)
                        ),
                    }
                    self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
                elif url.path == "/api/documents":
                    self._send(200, json.dumps(documents.listing()).encode("utf-8"),
                               "application/json")
                elif url.path == "/api/check":
                    body = {"findings": workspace.check(self._page_index(url.query))}
                    self._send(200, json.dumps(body).encode("utf-8"), "application/json")
                elif url.path == "/api/image":
                    self._send(200, workspace.image(self._page_index(url.query)), "image/png")
                else:
                    self._send(404, b"not found", "text/plain")
            except Exception as exc:  # noqa: BLE001 - one bad page must not kill the server
                self._fail(500, exc)

        def _element(self, element, index: int) -> None:
            """Every mutating route answers the same shape: the element plus
            the depth of the history, so the viewer never has to guess."""
            body = {
                "element": json.loads(element.model_dump_json(exclude_none=True)),
                "history": documents.current().history(index),
            }
            self._send(200, json.dumps(body).encode("utf-8"), "application/json")

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
            workspace = documents.current()
            url = urlparse(self.path)
            routes = ("/api/edit", "/api/move", "/api/retype", "/api/suggest",
                      "/api/undo", "/api/redo", "/api/export", "/api/import",
                      "/api/open")
            if url.path not in routes:
                self._send(404, b"not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_EDIT_BYTES:
                    # Answering without touching the body resets the connection
                    # and the client sees a transport error instead of the 413.
                    # Drain only a bounded prefix, then hang up.
                    self.rfile.read(min(length, MAX_EDIT_BYTES))
                    self.close_connection = True
                    self._send(413, b'{"error": "edit too large"}', "application/json")
                    return
                body = json.loads(self.rfile.read(length) or b"{}")
                index = self._page_index(url.query)

                if url.path == "/api/open":
                    # Switching keeps the previous Workspace alive, so an
                    # unexported correction is still there on the way back.
                    documents.select(int(body.get("index", 0)))
                    self._send(200, json.dumps(documents.listing()).encode(),
                               "application/json")
                elif url.path == "/api/export":
                    path = workspace.export(body.get("path"))
                    self._send(200, json.dumps({"path": str(path)}).encode(),
                               "application/json")
                elif url.path == "/api/import":
                    report = workspace.restore(body.get("path"))
                    report["history"] = workspace.history(index)
                    self._send(200, json.dumps(report).encode(), "application/json")
                elif url.path == "/api/undo":
                    self._element(workspace.undo(index), index)
                elif url.path == "/api/redo":
                    self._element(workspace.redo(index), index)
                elif url.path == "/api/suggest":
                    body_out = {"suggestions": workspace.suggest_headings(index)}
                    self._send(200, json.dumps(body_out).encode(), "application/json")
                elif url.path == "/api/retype":
                    self._element(workspace.retype(index, body["id"], body["type"]), index)
                elif url.path == "/api/move":
                    self._element(
                        workspace.move(index, body["id"], BoundingBox(**body["bbox"])),
                        index,
                    )
                else:
                    self._element(workspace.edit(index, body["id"], body["text"]), index)
            except KeyError as exc:
                self._fail(404, exc)
            except IndexError as exc:
                # An empty history is a normal state, not a server fault.
                self._fail(409, exc)
            except ValidationError as exc:
                self._fail(422, exc)
            except Exception as exc:  # noqa: BLE001 - a bad edit must not kill the server
                self._fail(500, exc)

        def _fail(self, status: int, exc: BaseException) -> None:
            body = json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode("utf-8")
            self._send(status, body, "application/json")

    return Handler


class DocumentSet:
    """One PDF or a folder of them, with a `Workspace` per document.

    A folder of scanned circulars is the shape the corpus actually arrives in,
    and reviewing them one `python -m docyx.workspace` at a time throws the
    session away between documents. So the set holds each `Workspace` open
    once created: the per-page cache is the session's working copy, and
    dropping it on a document switch would silently discard every correction
    made there.

    ponytail: that means memory grows with documents opened, roughly one
    extracted Document each. Fine for the tens of files a person reviews in a
    sitting; add an LRU that refuses to evict an edited document if someone
    opens a folder of thousands.
    """

    def __init__(self, path: str, pipeline: Optional[DocyxPipeline] = None):
        root = Path(path)
        if root.is_dir():
            # Sorted, so the order on screen is the order in the folder rather
            # than whatever the filesystem happens to return.
            self.paths = sorted(p for p in root.iterdir() if p.suffix.lower() == ".pdf")
            if not self.paths:
                raise ValueError(f"{root} holds no PDFs")
        else:
            self.paths = [root]
        self._pipeline = pipeline
        self._open: Dict[int, Workspace] = {}
        self.index = 0

    def current(self) -> Workspace:
        if self.index not in self._open:
            self._open[self.index] = Workspace(str(self.paths[self.index]), self._pipeline)
        return self._open[self.index]

    def select(self, index: int) -> Workspace:
        self.index = max(0, min(index, len(self.paths) - 1))
        return self.current()

    def listing(self) -> Dict[str, Any]:
        return {
            "documents": [p.name for p in self.paths],
            "current": self.index,
            # The viewer hides the picker entirely for a single document, and
            # needs to know that before it has anything else to go on.
            "edited": sorted(i for i, w in self._open.items() if w.edited()),
        }

    def close(self) -> None:
        for workspace in self._open.values():
            workspace.close()


def _port_is_taken(port: int) -> bool:
    """Is something already serving here?

    `ThreadingHTTPServer` sets SO_REUSEADDR, which on Windows lets a second
    bind SUCCEED on a port that is already in use. The second server then sits
    in serve_forever answering nothing while the first keeps the connections --
    so starting a workspace twice looked like it worked and showed the other
    document. Connecting is the portable check: a socket merely in TIME_WAIT
    does not accept, so this does not break an immediate restart.
    """
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def serve(pdf_path: str, port: int = 8000, open_browser: bool = True,
          pipeline: Optional[DocyxPipeline] = None) -> None:
    if _port_is_taken(port):
        raise OSError(f"port {port} is already serving; use --port to pick another")
    documents = DocumentSet(pdf_path, pipeline)
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(documents))
    url = f"http://127.0.0.1:{port}/"
    count = len(documents.paths)
    where = f"{count} documents" if count > 1 else f"{documents.current().page_count} pages"
    print(f"docyx workspace: {url}  ({where})")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
        documents.close()
