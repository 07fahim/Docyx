"""Fetch candidate scanned PDFs from the Internet Archive and keep only real ones.

The OCR evidence here is capped by the corpus, not by the harness:
`find_scanned_pages.py` says 1748 local pages yield exactly two gradeable
scans. Growing it needs documents, and the documents that matter are the ones
this project exists for -- Bengali and Arabic pages whose content is only in
their pixels.

    PYTHONPATH=. python scripts/fetch_scans.py ben 8
    PYTHONPATH=. python scripts/fetch_scans.py ara 8

**It verifies rather than assumes.** Most PDFs on the Archive carry an OCR text
layer the Archive added itself, which passes the gate and is useless as OCR
evidence -- grading a recogniser against another recogniser's output measures
agreement, not accuracy. So every download is run through the same gate the
pipeline uses and **deleted unless it holds a page with no text layer at all**.
That is the only property that makes a page gradeable here.

Downloads land in `.corpus/scans/`, which is gitignored like the rest of the
corpus: this fetches public-domain and openly-licensed material, and a corpus
is evidence to be reproduced rather than source to be committed. The identifier
of every kept document is recorded in `.corpus/scans/PROVENANCE.json` so the
set can be rebuilt.

Size-capped on purpose. A scanned book is hundreds of megabytes and adds one
more producer, the same as a twelve-page circular does.
"""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from docyx.pdf.renderer import PDFRenderer
from docyx.pipeline.gate import TextLayerGate

DEST = Path(".corpus/scans")
LEDGER = DEST / "PROVENANCE.json"

#: Bytes. A scanned book is hundreds of MB and buys exactly what a scanned
#: circular buys: one more producer, one more scanner, one more script.
MAX_BYTES = 25 * 1024 * 1024

#: How many pages to inspect before giving up on a document. A 400-page scan
#: proves itself on page 3; a born-digital one with a stray image does not
#: become a scan at page 200.
PROBE_PAGES = 12

AGENT = {"User-Agent": "docyx-corpus-builder (OCR evaluation; contact via repo)"}


def _get(url: str, params=None, timeout=90) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    request = urllib.request.Request(url, headers=AGENT)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def candidates(language: str, rows: int):
    """Identifiers of Archive text items in one language, newest first."""
    payload = _get("https://archive.org/advancedsearch.php", {
        "q": f"language:({language}) AND mediatype:texts AND format:(PDF)",
        "fl[]": ["identifier", "title"],
        "rows": rows,
        "page": 1,
        "output": "json",
        "sort[]": "downloads desc",
    })
    return json.loads(payload)["response"]["docs"]


def smallest_pdf(identifier: str):
    """(filename, bytes) of the smallest PDF in an item, or None.

    Smallest because the Archive usually offers several renderings of the same
    scan and the cheapest one has the same pixels.
    """
    try:
        meta = json.loads(_get(f"https://archive.org/metadata/{identifier}"))
    except Exception:
        return None
    pdfs = [
        (f["name"], int(f.get("size", 0)))
        for f in meta.get("files", [])
        if f["name"].lower().endswith(".pdf") and int(f.get("size", 0) or 0) > 0
    ]
    if not pdfs:
        return None
    name, size = min(pdfs, key=lambda p: p[1])
    return (name, size) if size <= MAX_BYTES else None


def scanned_pages(path: Path):
    """Page numbers with no text layer but raster content, probing the front."""
    gate = TextLayerGate()
    found = []
    with PDFRenderer(str(path)) as renderer:
        doc = renderer.text_document()
        producer = (doc.metadata or {}).get("producer") or "unknown"
        for page_num in range(min(renderer.page_count(), PROBE_PAGES)):
            try:
                if not gate.check_page(doc, page_num).passed and renderer.has_images(page_num):
                    found.append(page_num)
            except Exception:
                continue
    return found, producer


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    language = sys.argv[1]
    want = int(sys.argv[2]) if len(sys.argv) > 2 else 6

    DEST.mkdir(parents=True, exist_ok=True)
    ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}

    kept = 0
    for doc in candidates(language, rows=want * 12):
        if kept >= want:
            break
        identifier = doc["identifier"]
        if identifier in ledger:
            continue
        chosen = smallest_pdf(identifier)
        if not chosen:
            continue
        name, size = chosen
        target = DEST / f"{language}_{identifier[:48]}.pdf"
        try:
            url = (f"https://archive.org/download/{urllib.parse.quote(identifier)}"
                   f"/{urllib.parse.quote(name)}")
            target.write_bytes(_get(url, timeout=300))
            pages, producer = scanned_pages(target)
        except Exception as exc:
            target.unlink(missing_ok=True)
            print(f"  skip  {identifier[:44]:46s} {type(exc).__name__}")
            continue

        if not pages:
            # The common case: the Archive already added an OCR text layer, so
            # the page passes the gate and cannot grade a recogniser.
            target.unlink()
            print(f"  drop  {identifier[:44]:46s} has a text layer")
            continue

        ledger[identifier] = {
            "file": target.name, "language": language, "archive_file": name,
            "bytes": size, "producer": producer, "scanned_pages": pages,
            "title": str(doc.get("title", ""))[:120],
        }
        kept += 1
        print(f"  KEEP  {target.name[:46]:46s} {len(pages)} scanned of first "
              f"{PROBE_PAGES}  [{producer}]")

    LEDGER.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nkept {kept} new; {len(ledger)} documents in {LEDGER}")
    return 0 if kept else 1


if __name__ == "__main__":
    raise SystemExit(main())
