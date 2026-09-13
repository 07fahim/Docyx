"""Emit Docling's reading order for corpus pages, for cross-tool comparison.

Runs under .venv-bench, NOT .venv. Docling pulls torch, transformers and model
weights; putting those in the main environment would destroy the "core stays at
4 light dependencies" claim that is one of Docyx's few real differentiators.

    .venv-bench/Scripts/python.exe scripts/docling_dump.py .corpus/arxiv_bert.pdf

Writes .corpus/docling/<stem>.json — text items in Docling's own document
order, tagged with page number. scripts/compare_tools.py consumes it from the
other venv, so the two never have to import each other.
"""

import json
import sys
from pathlib import Path

OUT_DIR = Path(".corpus/docling")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    pdf_path = Path(sys.argv[1])
    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(str(pdf_path))
    doc = result.document

    items = []
    for item, _level in doc.iterate_items():
        text = getattr(item, "text", None)
        if not text:
            continue
        prov = getattr(item, "prov", None)
        if not prov:
            continue
        page_no = prov[0].page_no  # Docling pages are 1-based.
        items.append(
            {
                "page": page_no - 1,
                "label": str(getattr(item, "label", "")),
                "text": text,
            }
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{pdf_path.stem}.json"
    out.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    pages = sorted({i["page"] for i in items})
    print(f"{pdf_path.name}: {len(items)} text items across {len(pages)} pages -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
