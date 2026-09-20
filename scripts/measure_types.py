"""Score semantic role assignment against hand-labelled ground truth.

Type editing and `analysis/headings.py` both shipped with nothing that could
say whether their output is right -- the same gap the reading-order harness was
built to close. This closes it for `type`.

    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_types.py
    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_types.py wiki   # filter

Truth files live in `.corpus/truth/types/` and share their line inventory and
checksum with the reading-order truth, so labelling a page costs one worksheet
(`scripts/dump_lines.py`) rather than two. They record only the lines that are
NOT plain text, because "everything is text" is precisely the baseline under
measurement and writing it out 300 times would obscure the exceptions.

**Accuracy is not reported, and that is the whole point.** 88% of the labelled
lines are `text`, so a predictor that answers "text" to everything -- which is
exactly what Docyx does with no layout model -- scores 0.88 and has found
nothing. Per class precision and recall, against that baseline, is the only
reading that can tell the two apart.

Two predictors are scored side by side:

- `bare`       what the pipeline emits today: every line is `text`.
- `suggested`  the same page with `analysis/headings.py` proposals applied,
               which is what the workspace's `Suggest` button does.

`suggested` can only ever differ on `title` and `section_header`; the caption,
page-header, page-footer and table rows are there to show what is still
unreachable without a layout model, and to catch a future heuristic that buys
its heading recall by mislabelling them.
"""

import json
import sys
from collections import Counter
from pathlib import Path

from docyx.analysis.headings import suggest
from docyx.analysis.reading_order import is_orderable
from docyx.pipeline.extractor import DocyxPipeline

TRUTH_DIR = Path(".corpus/truth/types")
CORPUS_DIR = Path(".corpus")

#: Reported in this order, headings first because they are what is predicted.
CLASSES = ["title", "section_header", "caption", "page_header", "page_footer", "table", "text"]


def neutral_lines(page):
    """Must match scripts/dump_lines.py -- the truth indices are its numbering."""
    text = [el for el in page.elements if is_orderable(el)]
    return sorted(text, key=lambda el: (el.geometry.bbox.y, el.geometry.bbox.x))


def load_truth(path: Path) -> dict:
    truth = json.loads(path.read_text(encoding="utf-8"))
    unknown = set(truth["types"].values()) - set(CLASSES)
    if unknown:
        raise SystemExit(f"{path.name}: labels outside the vocabulary: {sorted(unknown)}")
    return truth


def predictions(truth: dict):
    """(truth labels, bare prediction, suggested prediction) -- three equal-length lists."""
    from scripts.dump_lines import checksum

    pdf = CORPUS_DIR / truth["document"]
    doc = DocyxPipeline().process(str(pdf), document_id=truth["document"], pages=[truth["page"]])
    page = doc.pages[0]
    lines = neutral_lines(page)

    actual = checksum(lines)
    if actual != truth["checksum"]:
        raise SystemExit(
            f"{truth['document']} p{truth['page']}: line inventory changed "
            f"(truth={truth['checksum']} now={actual}). The labels no longer refer "
            f"to the same lines -- re-label with dump_lines.py."
        )

    labels = [truth["types"].get(str(i), truth["default"]) for i in range(len(lines))]
    bare = [el.type for el in lines]

    proposed = dict(suggest(page.elements))
    suggested = [proposed.get(el.id, el.type) for el in lines]
    return labels, bare, suggested


def counts(labels, predicted):
    """(true positives, predicted, actual) per class."""
    tp = Counter()
    for truth_label, pred in zip(labels, predicted):
        if truth_label == pred:
            tp[truth_label] += 1
    return tp, Counter(predicted), Counter(labels)


def f1(tp: int, predicted: int, actual: int):
    precision = tp / predicted if predicted else None
    recall = tp / actual if actual else None
    if not precision or not recall:
        return precision, recall, None
    return precision, recall, 2 * precision * recall / (precision + recall)


def cell(value) -> str:
    return "     -" if value is None else f"{value:6.2f}"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    needle = sys.argv[1] if len(sys.argv) > 1 else ""
    paths = sorted(p for p in TRUTH_DIR.glob("*.json") if needle in p.name)
    if not paths:
        print(f"No truth files in {TRUTH_DIR} matching {needle!r}")
        return 2

    labels, bare, suggested = [], [], []
    for path in paths:
        truth = load_truth(path)
        page_labels, page_bare, page_suggested = predictions(truth)
        print(f"  {truth['document']:20s} p{truth['page']:<4d} {len(page_labels):3d} lines")
        labels += page_labels
        bare += page_bare
        suggested += page_suggested

    print(f"\n{len(labels)} lines over {len(paths)} pages\n")
    print(f"{'class':16s} {'n':>4s} | {'bare P':>6s} {'R':>6s} {'F1':>6s}"
          f" | {'sugg P':>6s} {'R':>6s} {'F1':>6s}")

    bare_counts = counts(labels, bare)
    sugg_counts = counts(labels, suggested)
    actual = Counter(labels)
    for name in CLASSES:
        if not actual[name]:
            continue
        row = f"{name:16s} {actual[name]:4d} |"
        for tp, predicted, _ in (bare_counts, sugg_counts):
            p, r, f = f1(tp[name], predicted[name], actual[name])
            row += f" {cell(p)} {cell(r)} {cell(f)} |"
        print(row)

    # Macro F1 over the classes that actually occur. Deliberately NOT accuracy:
    # "text" for every line scores 0.88 here and has found nothing.
    for title, (tp, predicted, _) in (("bare", bare_counts), ("suggested", sugg_counts)):
        scores = [
            f1(tp[n], predicted[n], actual[n])[2] or 0.0
            for n in CLASSES if actual[n]
        ]
        share = sum(tp[n] for n in CLASSES) / len(labels)
        print(f"\n  {title:10s} macro F1 {sum(scores) / len(scores):.3f}"
              f"   (accuracy {share:.3f}, which is why macro F1 is the number)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
