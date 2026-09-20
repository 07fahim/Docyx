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
from typing import Optional
from urllib.parse import parse_qs, urlparse

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

    def page(self, index: int) -> Document:
        if index not in self._pages:
            self._pages[index] = self.pipeline.process(
                self.pdf_path, Path(self.pdf_path).stem, pages=[index]
            )
        return self._pages[index]

    def image(self, index: int) -> bytes:
        return self.renderer.render_page(index)

    def edit(self, index: int, element_id: str, text: str) -> Element:
        """Apply a human correction through `edit_text`, never by assignment.

        The edit lands on the cached `Document`, so it survives navigating
        away and back — the cache is the session's working copy.
        """
        for element in _walk(self.page(index).pages[0]):
            if element.id == element_id:
                return element.edit_text(text)
        raise KeyError(element_id)

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

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
            url = urlparse(self.path)
            if url.path != "/api/edit":
                self._send(404, b"not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_EDIT_BYTES:
                    self._send(413, b'{"error": "edit too large"}', "application/json")
                    return
                body = json.loads(self.rfile.read(length) or b"{}")
                element = workspace.edit(
                    self._page_index(url.query), body["id"], body["text"]
                )
                self._send(
                    200,
                    element.model_dump_json(exclude_none=True).encode("utf-8"),
                    "application/json",
                )
            except KeyError as exc:
                self._fail(404, exc)
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
