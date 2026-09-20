"""Score recovered table structure against hand-labelled ground truth.

`TableAnalyzer` ships a real Table Transformer through the `detector` seam and
nothing could say whether its grid was right -- the last capability in the repo
still in the trap the reading-order harness was built to close.

    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_tables.py
    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_tables.py nasa   # filter

Needs the optional model stack (`pip install -r requirements-models.txt`).

**A cell is a set of text lines, not a string.** Docyx builds cell text by
joining native lines that fall inside the cell box, so "did every line land in
the right cell" is the property that decides whether the table is right, and it
is the one a text comparison cannot measure honestly: a multi-line cell differs
from its reference by spacing and join order long before it differs in content.
Truth files therefore record line INDICES from `scripts/dump_lines.py`, share
its checksum, and are invalidated by the same extraction change.

Two numbers, because they fail differently:

- `cell`  A predicted cell counts only when its line set EXACTLY equals a truth
          cell's. Strict, and blind to structure: a table shredded into correct
          single-line cells and reassembled in the wrong order scores well.
- `adj`   The ICDAR-style adjacency metric. Every truth cell's nearest occupied
          neighbour to the right and below is a relation; precision and recall
          run over those. This is the structural number -- it is what notices
          that two cells the model got individually right are no longer beside
          each other.

**Quote them together**, and against the baseline: rows and columns inferred by
clustering line positions, which is what geometry alone can do. "We ran a
model" is not a result if clustering by x and y recovers the same grid free.

`cov` is the share of labelled table lines the model placed in some cell. It
exists because the baseline is HANDED the table's lines and the model has to
find them, so without it "naive wins" cannot be read: a model losing at
coverage 1.0 got the structure wrong, one losing at low coverage never saw the
rows at all. Those are different bugs with different fixes.
"""

import json
import sys
from collections import Counter
from pathlib import Path

from docyx.analysis.reading_order import is_orderable
from docyx.analysis.tables import TableAnalyzer
from docyx.pipeline.extractor import DocyxPipeline

TRUTH_DIR = Path(".corpus/truth/tables")
CORPUS_DIR = Path(".corpus")


def neutral_lines(page):
    """Must match scripts/dump_lines.py -- truth indices are its numbering."""
    text = [el for el in page.elements if is_orderable(el)]
    return sorted(text, key=lambda el: (el.geometry.bbox.y, el.geometry.bbox.x))


def truth_cells(grid):
    """{(row, column): frozenset(line indices)} for every non-empty cell."""
    return {
        (r, c): frozenset(lines)
        for r, row in enumerate(grid)
        for c, lines in enumerate(row)
        if lines
    }


def lines_in(bbox, lines) -> frozenset:
    """Line indices whose centre falls inside the box -- the same containment
    rule `_populate_cell_text` uses to fill a cell, so this measures the
    assignment the pipeline actually makes."""
    inside = set()
    for index, line in enumerate(lines):
        box = line.geometry.bbox
        if bbox.contains(box.x + box.width / 2, box.y + box.height / 2):
            inside.add(index)
    return frozenset(inside)


def predicted_cells(page, lines):
    """{(row, column): frozenset(line indices)} over every detected table."""
    out = {}
    for table in (el for el in page.elements if el.type == "table"):
        for cell in table.children:
            if cell.grid is None:
                continue
            content = lines_in(cell.geometry.bbox, lines)
            if content:
                out[(cell.grid.row, cell.grid.column)] = content
    return out


def _cluster(values, gap: float):
    """Indices grouped where consecutive sorted starts are closer than `gap`."""
    groups, current = [], []
    for key, index in sorted(values):
        if current and key - current[-1][0] > gap:
            groups.append(current)
            current = []
        current.append((key, index))
    if current:
        groups.append(current)
    return [[index for _, index in group] for group in groups]


def geometry_grid(lines, used) -> dict:
    """The baseline: rows and columns inferred by clustering line positions.

    This is what geometry alone can do, and it is the claim a table model has
    to beat -- "we ran a model" is not a result if clustering by x and y gets
    the same grid for free.

    It is handed the table's own lines rather than having to find the region,
    which is deliberately generous: the question under test is whether the
    model recovers STRUCTURE better, not whether it locates tables better, and
    a baseline that has to do both would lose for the wrong reason.
    """
    if not used:
        return {}
    boxes = {index: lines[index].geometry.bbox for index in used}
    height = sorted(b.height for b in boxes.values())[len(boxes) // 2]
    rows = _cluster([(b.y, i) for i, b in boxes.items()], height * 0.6)
    columns = _cluster([(b.x, i) for i, b in boxes.items()], height * 1.5)

    row_of = {index: r for r, group in enumerate(rows) for index in group}
    column_of = {index: c for c, group in enumerate(columns) for index in group}

    cells: dict = {}
    for index in used:
        cells.setdefault((row_of[index], column_of[index]), set()).add(index)
    return {key: frozenset(value) for key, value in cells.items()}


def relations(cells):
    """ICDAR-style adjacency: each cell's nearest occupied neighbour right and
    below, keyed by CONTENT so the two grids never have to agree on numbering.

    Numbering is exactly what a missed header row shifts, and a metric that
    punished that twice -- once as a lost cell, once as every relation below it
    -- would report a one-row error as total failure.
    """
    by_position = dict(cells)
    rows = sorted({r for r, _ in by_position})
    columns = sorted({c for _, c in by_position})
    found = Counter()
    for (r, c), content in by_position.items():
        for axis, step in (("right", 1), ("down", 0)):
            line = columns if step else rows
            index = line.index(c if step else r)
            for nxt in line[index + 1:]:
                other = by_position.get((r, nxt) if step else (nxt, c))
                if other:
                    found[(content, other, axis)] += 1
                    break
    return found


def prf(matched: int, predicted: int, actual: int):
    precision = matched / predicted if predicted else 0.0
    recall = matched / actual if actual else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
    return precision, recall, f1


def score(truth, predicted):
    """(cell P/R/F1, adjacency P/R/F1)."""
    truth_sets = Counter(truth.values())
    predicted_sets = Counter(predicted.values())
    cell = prf(sum((truth_sets & predicted_sets).values()),
               sum(predicted_sets.values()), sum(truth_sets.values()))

    a, b = relations(truth), relations(predicted)
    adjacency = prf(sum((a & b).values()), sum(b.values()), sum(a.values()))
    return cell, adjacency


def score_page(path: Path, analyzer: TableAnalyzer) -> dict:
    from scripts.dump_lines import checksum

    truth = json.loads(path.read_text(encoding="utf-8"))
    if "grid" not in truth:
        raise SystemExit(
            f"{path.name} has no 'grid': this is not a table truth file. "
            "One directory per scorer -- region samples live in "
            "truth/table_regions/."
        )
    pdf = CORPUS_DIR / truth["document"]
    page = DocyxPipeline(table_analyzer=analyzer).process(
        str(pdf), truth["document"], pages=[truth["page"]]
    ).pages[0]
    lines = neutral_lines(page)

    actual = checksum(lines)
    if actual != truth["checksum"]:
        raise SystemExit(
            f"{truth['document']} p{truth['page']}: line inventory changed "
            f"(truth={truth['checksum']} now={actual}). The labelled cells no "
            f"longer refer to the same lines -- re-label with dump_lines.py."
        )

    cells = truth_cells(truth["grid"])
    used = {index for content in cells.values() for index in content}
    predicted = predicted_cells(page, lines)

    # The shipped model-free path, scored the honest way: it is given nothing,
    # and has to find the region itself. The `naive` column below is handed the
    # table's lines, so only this one is a like-for-like comparison with the
    # model beside it.
    shipped_page = DocyxPipeline(table_geometry=True).process(
        str(pdf), truth["document"], pages=[truth["page"]]
    ).pages[0]
    shipped = predicted_cells(shipped_page, neutral_lines(shipped_page))

    # The baseline is handed `used`; the model had to find the table itself.
    # Coverage separates those two failures, so "naive wins" can be read
    # correctly: a model that scores badly with coverage 1.0 got the structure
    # wrong, and one that scores badly with low coverage never saw the rows.
    covered = {index for content in predicted.values() for index in content}

    return {
        "page": f"{truth['document']} p{truth['page']}",
        "shape": f"{len(truth['grid'])}x{len(truth['grid'][0])}",
        "cells": len(cells),
        "found": len(predicted),
        "coverage": len(covered & used) / len(used) if used else 0.0,
        "model": score(cells, predicted),
        "shipped": score(cells, shipped),
        "naive": score(cells, geometry_grid(lines, used)),
        "warnings": [w.code for w in page.warnings],
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        from docyx.analysis.detectors.table_transformer import TableTransformerDetector

        analyzer = TableAnalyzer(detector=TableTransformerDetector())
    except ImportError as exc:
        print(f"needs the optional model stack: {exc}")
        print("pip install -r requirements-models.txt")
        return 2

    needle = sys.argv[1] if len(sys.argv) > 1 else ""
    paths = sorted(p for p in TRUTH_DIR.glob("*.json") if needle in p.name)
    if not paths:
        print(f"No truth files in {TRUTH_DIR} matching {needle!r}")
        return 2

    results = [score_page(p, analyzer) for p in paths]

    print(f"\n{'table':22s} {'shape':>6s} {'cells':>5s} {'found':>5s} {'cov':>5s} |"
          f" {'cell P':>6s} {'R':>6s} {'F1':>6s} | {'adj P':>6s} {'R':>6s} {'F1':>6s}"
          f" | {'shipped':>7s} | {'naive':>6s}")
    for r in results:
        cp, cr, cf = r["model"][0]
        ap, ar, af = r["model"][1]
        flag = "" if af > r["naive"][1][2] + 0.001 else "  <- naive geometry does this too"
        print(f"{r['page']:22s} {r['shape']:>6s} {r['cells']:5d} {r['found']:5d}"
              f" {r['coverage']:5.2f} |"
              f" {cp:6.3f} {cr:6.3f} {cf:6.3f} | {ap:6.3f} {ar:6.3f} {af:6.3f}"
              f" | {r['shipped'][1][2]:7.3f} | {r['naive'][1][2]:6.3f}{flag}")
        if r["warnings"]:
            print(f"{'':22s} warnings: {', '.join(r['warnings'])}")

    def mean(pick):
        return sum(pick(r) for r in results) / len(results)

    print(f"\n  model   adj F1 {mean(lambda r: r['model'][1][2]):.3f}"
          "   needs torch + 110M weights, and is handed nothing else")
    print(f"  shipped adj F1 {mean(lambda r: r['shipped'][1][2]):.3f}"
          "   model-free, finds its own region  <- the like-for-like comparison")
    print(f"  naive   adj F1 {mean(lambda r: r['naive'][1][2]):.3f}"
          "   handed the table's lines; an upper bound, not a competitor")
    print("  quote both: cell F1 is blind to a table shredded into correct pieces")
    print("  `cov` is the share of labelled table lines the model put in SOME cell,")
    print("  which separates 'never saw the rows' from 'got the structure wrong'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
