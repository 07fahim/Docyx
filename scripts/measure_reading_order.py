"""Score reading order against hand-labelled ground truth.

This replaces the old sweep, which measured agreement with PyMuPDF's own
reading order. That metric is circular: PyMuPDF is a dependency, so the ceiling
was "equal PyMuPDF", and a page where Docyx legitimately beats it — a
two-column paper, say — scored as a regression. Truth files in .corpus/truth/
are ordered by hand instead, so the number means something.

    python scripts/measure_reading_order.py              # every truth file
    python scripts/measure_reading_order.py arxiv_bert   # filter by substring

Two metrics, because they fail differently:

- `tau`   Kendall rank correlation. Global. Tolerates a locally misplaced line,
          punishes reading the columns in the wrong order.
- `adj`   Fraction of consecutive truth pairs that are also consecutive and in
          order in the prediction. Local: it sees line-level shuffling.

**Read them together; neither is sufficient.** adj is nearly blind to the
catastrophe it looks like it should catch: swap the top and bottom halves of a
page — unreadable output — and adj still scores ~0.99, because all but one
adjacent pair survives. tau drops to about -0.01 on the same input. Conversely
tau tolerates local shuffling that destroys prose. Reporting adj alone would
have hidden a column-order failure completely.
"""

import json
import sys
from pathlib import Path

from docyx.analysis.reading_order import ORDERABLE_TYPES
from docyx.pipeline.extractor import DocyxPipeline

TRUTH_DIR = Path(".corpus/truth")
CORPUS_DIR = Path(".corpus")


def neutral_lines(page):
    """Text elements in naive (y, x) order — must match scripts/dump_lines.py."""
    text = [el for el in page.elements if el.type in ORDERABLE_TYPES]
    return sorted(text, key=lambda el: (el.geometry.bbox.y, el.geometry.bbox.x))


def kendall_tau(truth_rank, pred_rank) -> float:
    """Concordant minus discordant pairs, normalised.

    ponytail: O(n^2) over lines on one page (~100, so ~5k pairs). Merge-sort
    counting only matters if pages get an order of magnitude denser.
    """
    items = list(truth_rank)
    concordant = discordant = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            truth_delta = truth_rank[a] - truth_rank[b]
            pred_delta = pred_rank[a] - pred_rank[b]
            if truth_delta * pred_delta > 0:
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total else 1.0


def adjacent_accuracy(order, pred_rank) -> float:
    """Fraction of truth-consecutive pairs that stay consecutive in prediction."""
    if len(order) < 2:
        return 1.0
    kept = sum(
        1
        for a, b in zip(order, order[1:])
        if pred_rank[b] == pred_rank[a] + 1
    )
    return kept / (len(order) - 1)


def load_truth(path: Path) -> dict:
    truth = json.loads(path.read_text(encoding="utf-8"))
    order = truth["order"]
    # A truth file with a repeated or missing index would silently produce a
    # meaningless score, so fail loudly instead — hand-labelling is error-prone.
    if sorted(order) != list(range(len(order))):
        missing = set(range(len(order))) - set(order)
        dupes = {i for i in order if order.count(i) > 1}
        raise SystemExit(
            f"{path.name}: order is not a permutation of 0..{len(order) - 1}. "
            f"missing={sorted(missing)} duplicated={sorted(dupes)}"
        )
    return truth


def score_page(truth: dict) -> dict:
    from scripts.dump_lines import checksum

    pdf = CORPUS_DIR / truth["document"]
    doc = DocyxPipeline().process(str(pdf), document_id=truth["document"])
    page = doc.pages[truth["page"]]
    lines = neutral_lines(page)

    actual = checksum(lines)
    if actual != truth["checksum"]:
        raise SystemExit(
            f"{truth['document']} p{truth['page']}: line inventory changed "
            f"(truth={truth['checksum']} now={actual}). The recorded order no "
            f"longer refers to the same lines — re-label with dump_lines.py."
        )

    order = truth["order"]
    if len(lines) != len(order):
        raise SystemExit(
            f"{truth['document']} p{truth['page']}: {len(lines)} lines but "
            f"{len(order)} labelled."
        )

    # Neutral index per element id, then Docyx's own ordering of those same ids.
    neutral_of = {el.id: i for i, el in enumerate(lines)}
    docyx_order = [
        neutral_of[el.id]
        for el in sorted(
            (el for el in page.elements if el.type in ORDERABLE_TYPES),
            key=lambda el: el.reading_order,
        )
    ]

    truth_rank = {line: rank for rank, line in enumerate(order)}
    pred_rank = {line: rank for rank, line in enumerate(docyx_order)}

    # The naive (y, x) order scored on the same page. A page where naive already
    # scores 1.000 proves nothing about the algorithm — it only guards against a
    # regression — and without this column such pages silently inflate the mean.
    naive_rank = {line: line for line in order}

    return {
        "page": f"{truth['document']} p{truth['page']}",
        "lines": len(lines),
        "tau": kendall_tau(truth_rank, pred_rank),
        "adj": adjacent_accuracy(order, pred_rank),
        "naive_adj": adjacent_accuracy(order, naive_rank),
        "naive_tau": kendall_tau(truth_rank, naive_rank),
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    needle = sys.argv[1] if len(sys.argv) > 1 else ""
    paths = sorted(p for p in TRUTH_DIR.glob("*.json") if needle in p.name)
    if not paths:
        print(f"No truth files in {TRUTH_DIR} matching {needle!r}")
        return 2

    results = [score_page(load_truth(p)) for p in paths]

    print(
        f"{'page':26s} {'lines':>5s} {'tau':>7s} {'naive':>7s} "
        f"{'adj':>7s} {'naive':>7s} {'gain':>6s}"
    )
    for r in results:
        gain = r["adj"] - r["naive_adj"]
        flag = "" if gain > 0.001 else "  <- no discriminating power"
        print(
            f"{r['page']:26s} {r['lines']:5d} {r['tau']:7.3f} {r['naive_tau']:7.3f} "
            f"{r['adj']:7.3f} {r['naive_adj']:7.3f} {gain:+6.3f}{flag}"
        )

    def mean(key):
        return sum(r[key] for r in results) / len(results)

    print(
        f"{'MEAN':26s} {'':5s} {mean('tau'):7.3f} {mean('naive_tau'):7.3f} "
        f"{mean('adj'):7.3f} {mean('naive_adj'):7.3f} "
        f"{mean('adj') - mean('naive_adj'):+6.3f}"
    )
    print()
    print(
        f"  gain over naive:  tau {mean('tau') - mean('naive_tau'):+.3f}"
        f"   adj {mean('adj') - mean('naive_adj'):+.3f}"
        "   <- quote both; adj has the larger spread"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
