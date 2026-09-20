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
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from docyx.core.geometry import BoundingBox
from docyx.pdf.renderer import PDFRenderer
from docyx.pipeline.extractor import DocyxPipeline
from docyx.schema.models import Document, Element, Page

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


def _snapshot(element: Element) -> Dict[str, Any]:
    """Everything an edit can touch, deep-copied.

    Undo restores this wholesale rather than re-applying `edit_text` in
    reverse: re-editing would leave `modified_by_user` set and the element
    `exact`/1.0, claiming a human vouched for a value they just took back.
    """
    return {
        "text": element.text,
        "geometry": element.geometry.model_copy(deep=True),
        "confidence": element.confidence.model_copy(deep=True),
        "provenance": element.provenance.model_copy(deep=True),
    }


def _restore(element: Element, state: Dict[str, Any]) -> Element:
    element.text = state["text"]
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

    def page(self, index: int) -> Document:
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
        return self._record(index, self.find(index, element_id)).edit_text(text)

    def move(self, index: int, element_id: str, bbox: BoundingBox) -> Element:
        """Correct an element's position or size through `edit_geometry` (§10)."""
        return self._record(index, self.find(index, element_id)).edit_geometry(bbox)

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
        stack = source.get(index) or []
        if not stack:
            raise IndexError("nothing to undo" if source is self._undo else "nothing to redo")
        element, state = stack.pop()
        sink.setdefault(index, []).append((element, _snapshot(element)))
        return _restore(element, state)

    def history(self, index: int) -> Dict[str, int]:
        return {"undo": len(self._undo.get(index) or []),
                "redo": len(self._redo.get(index) or [])}

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

    def close(self) -> None:
        self.renderer.close()


def _handler(workspace: Workspace):
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
            index = int(parse_qs(query).get("page", ["0"])[0])
            # Clamped rather than 404: the viewer's next/prev buttons are the
            # only caller, and an off-by-one there should not blank the screen.
            return max(0, min(index, workspace.page_count - 1))

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
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
                        "page": json.loads(
                            document.pages[0].model_dump_json(exclude_none=True)
                        ),
                    }
                    self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
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
                "history": workspace.history(index),
            }
            self._send(200, json.dumps(body).encode("utf-8"), "application/json")

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
            url = urlparse(self.path)
            routes = ("/api/edit", "/api/move", "/api/undo", "/api/redo", "/api/export")
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

                if url.path == "/api/export":
                    path = workspace.export(body.get("path"))
                    self._send(200, json.dumps({"path": str(path)}).encode(),
                               "application/json")
                elif url.path == "/api/undo":
                    self._element(workspace.undo(index), index)
                elif url.path == "/api/redo":
                    self._element(workspace.redo(index), index)
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


def serve(pdf_path: str, port: int = 8000, open_browser: bool = True,
          pipeline: Optional[DocyxPipeline] = None) -> None:
    workspace = Workspace(pdf_path, pipeline)
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(workspace))
    url = f"http://127.0.0.1:{port}/"
    print(f"docyx workspace: {url}  ({workspace.page_count} pages)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
        workspace.close()
