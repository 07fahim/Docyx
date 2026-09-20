"""Estimate table-detection precision from a random sample, with an interval.

`measure_table_regions.py` grades hand-picked adversarial pages, and that is a
bug-finding tool rather than a measurement: every time the list grew, a new
false-positive class appeared, so "nothing broke on my nine pages" was never
evidence that nothing breaks. It cannot converge, because the pages are chosen
by whoever already knows where the bugs are.

This measures the quantity that actually decides whether tables can be on by
default: **what share of the detections on an unseen page are real tables?**
Every page of the corpus is run, a random sample of the detections is drawn
with a fixed seed, a person adjudicates them, and the result is a precision
estimate with a Wilson confidence interval.

    PYTHONPATH=. python scripts/sample_table_detections.py draw 40
    # ... fill in "verdict": "table" | "not" for each entry ...
    PYTHONPATH=. python scripts/sample_table_detections.py score

**State the stopping rule before looking.** Turning this on by default is
justified when the *lower bound* of the interval clears the bar, not the point
estimate -- a run of 20 clean samples is 1.00 with a lower bound near 0.84,
which is not the same claim at all.

Recall is deliberately not estimated here. It would need every table in the
corpus labelled, and it is the less important number: a missed table costs a
feature, while a false one silently reorders a page that was correct.
"""

import json
import math
import random
import sys
from pathlib import Path

from docyx.pipeline.extractor import DocyxPipeline

CORPUS = Path(".corpus")
#: Its own directory. `measure_tables.py` globs `truth/tables/*.json` and
#: crashed on this file the moment it landed there -- the same collision the
#: OCR reference caused in the reading-order directory. One per scorer.
WORKSHEET = Path(".corpus/truth/table_regions/sample.json")

#: Precision the lower bound must clear before tables go on by default.
#: Set here, before any adjudication, so the bar cannot move to fit the result.
TARGET = 0.95


def wilson(successes: int, total: int, z: float = 1.96):
    """Confidence interval for a proportion.

    Wilson rather than the textbook normal approximation, which gives a zero
    width interval at 20 out of 20 -- exactly the case this has to report
    honestly.
    """
    if not total:
        return 0.0, 0.0, 0.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return p, max(0.0, centre - spread), min(1.0, centre + spread)


def every_detection():
    """(document, page, cell texts) for every table found in the corpus."""
    found = []
    for pdf in sorted(CORPUS.glob("*.pdf")):
        document = DocyxPipeline(table_geometry=True).process(str(pdf), pdf.name)
        for page in document.pages:
            for index, table in enumerate(
                el for el in page.elements if el.type == "table"
            ):
                found.append({
                    "document": pdf.name,
                    "page": page.page_number - 1,
                    "table": index,
                    "rows": max((c.grid.row for c in table.children), default=0) + 1,
                    "columns": max((c.grid.column for c in table.children), default=0) + 1,
                    "cells": [(c.text or "")[:40] for c in table.children[:24]],
                    "verdict": None,
                })
        print(f"  {pdf.name}", file=sys.stderr)
    return found


def draw(size: int, seed: int) -> int:
    population = every_detection()
    if not population:
        print("no detections in the corpus")
        return 2
    rng = random.Random(seed)
    sample = rng.sample(population, min(size, len(population)))
    WORKSHEET.parent.mkdir(parents=True, exist_ok=True)
    WORKSHEET.write_text(
        json.dumps(
            {"seed": seed, "population": len(population), "sample": sample},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"{len(population)} detections across the corpus; "
          f"{len(sample)} drawn into {WORKSHEET}")
    print('Set each "verdict" to "table" or "not", then run: score')
    return 0


def score() -> int:
    if not WORKSHEET.exists():
        print(f"{WORKSHEET} does not exist; run `draw` first")
        return 2
    data = json.loads(WORKSHEET.read_text(encoding="utf-8"))
    sample = data["sample"]
    unjudged = [s for s in sample if s["verdict"] not in ("table", "not")]
    if unjudged:
        print(f"{len(unjudged)} of {len(sample)} entries still have no verdict")
        return 2

    real = sum(1 for s in sample if s["verdict"] == "table")
    point, low, high = wilson(real, len(sample))

    print(f"population   {data['population']} detections over the whole corpus")
    print(f"sample       {len(sample)} drawn with seed {data['seed']}")
    print(f"precision    {point:.3f}   95% CI [{low:.3f}, {high:.3f}]")
    print()
    for entry in sample:
        if entry["verdict"] == "not":
            print(f"  FALSE  {entry['document']} p{entry['page']} "
                  f"{entry['rows']}x{entry['columns']}  {entry['cells'][:4]}")

    verdict = "clears" if low >= TARGET else "does NOT clear"
    print(f"\n  lower bound {low:.3f} {verdict} the {TARGET} bar set before "
          "adjudication.")
    print("  The LOWER BOUND decides, not the point estimate: 20 clean samples "
          "read 1.000 with a bound near 0.84.")
    return 0 if low >= TARGET else 1


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2 or sys.argv[1] not in ("draw", "score"):
        print(__doc__)
        return 2
    if sys.argv[1] == "score":
        return score()
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 20260920
    return draw(size, seed)


if __name__ == "__main__":
    raise SystemExit(main())
