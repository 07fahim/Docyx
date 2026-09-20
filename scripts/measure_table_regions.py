"""Does the model-free table finder fire on tables, and only on tables?

`scripts/measure_tables.py` grades table STRUCTURE given the table's lines.
This grades the part that comes first and is much harder: deciding which lines
form a table at all. It is the reason `TableAnalyzer._heuristic` returned `[]`
for four phases.

    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_table_regions.py

No models, no truth files. Recall comes from the three labelled tables in
`.corpus/truth/tables/`; precision comes from pages known to hold no table at
all, because **the failure that matters here is a false positive**. A missed
table costs a feature; a two-column paper reported as a table corrupts the
reading order of a page that was previously correct, and does it silently.
"""

import json
import sys
from pathlib import Path

from docyx.analysis.reading_order import is_orderable
from docyx.analysis.table_geometry import find_tables
from docyx.pipeline.extractor import DocyxPipeline

CORPUS = Path(".corpus")
TRUTH = Path(".corpus/truth/tables")

#: Pages with no table on them. Each is a shape that could plausibly be
#: mistaken for one: two-column prose, a monospace spec with indented grammar
#: listings, a dense RTL encyclopaedia page, and a form of labelled boxes.
NO_TABLE = [
    ("arxiv_attention.pdf", 2, "two-column prose"),
    ("arxiv_bert.pdf", 3, "two-column prose"),
    ("rfc2616.pdf", 12, "monospace spec, indented grammar blocks"),
    ("wiki_ar.pdf", 6, "RTL encyclopaedia, figure float"),
    ("wiki_bn.pdf", 5, "Bengali encyclopaedia, image captions"),
    ("arxiv_attention.pdf", 0, "title page, author/affiliation grid"),
    ("arxiv_bert.pdf", 0, "two-column abstract at a narrower indent"),
    ("arxiv_bert.pdf", 2, "architecture figure, labels at arbitrary x"),
    ("arxiv_attention.pdf", 4, "displayed equations"),
]


def lines_of(pdf: str, page_num: int):
    page = DocyxPipeline().process(str(CORPUS / pdf), pdf, pages=[page_num]).pages[0]
    text = [el for el in page.elements if is_orderable(el)]
    return sorted(text, key=lambda el: (el.geometry.bbox.y, el.geometry.bbox.x))


def covered(table, lines, wanted) -> float:
    """Share of the labelled table's lines that fall inside the found table."""
    box = table.geometry.bbox
    hit = {
        index for index in wanted
        if box.contains(
            lines[index].geometry.bbox.x + lines[index].geometry.bbox.width / 2,
            lines[index].geometry.bbox.y + lines[index].geometry.bbox.height / 2,
        )
    }
    return len(hit) / len(wanted) if wanted else 0.0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("RECALL — pages with a labelled table\n")
    print(f"{'page':24s} {'found':>5s} {'coverage':>9s}   notes")
    recalls = []
    for path in sorted(TRUTH.glob("*.json")):
        truth = json.loads(path.read_text(encoding="utf-8"))
        lines = lines_of(truth["document"], truth["page"])
        wanted = {i for row in truth["grid"] for cell in row for i in cell}
        tables = find_tables(lines, truth["page"])
        best = max((covered(t, lines, wanted) for t in tables), default=0.0)
        recalls.append(best)
        print(f"{truth['document'] + ' p' + str(truth['page']):24s} "
              f"{len(tables):5d} {best:9.2f}")

    print("\nPRECISION — pages with no table at all\n")
    print(f"{'page':24s} {'found':>5s}   shape")
    false_positives = 0
    for pdf, page_num, note in NO_TABLE:
        lines = lines_of(pdf, page_num)
        tables = find_tables(lines, page_num)
        false_positives += len(tables)
        flag = "  <- FALSE POSITIVE" if tables else ""
        print(f"{pdf + ' p' + str(page_num):24s} {len(tables):5d}   {note}{flag}")

    print(f"\n  mean coverage on real tables  {sum(recalls) / len(recalls):.2f}")
    print(f"  false tables on clean pages   {false_positives}"
          "   <- this is the number that has to be 0")
    return 0 if false_positives == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
