"""Does grouping lines by detected layout region beat plain XY-cut?

An EXPERIMENT, not a feature. It scores a region-aware ordering against the
same hand-labelled truth files `measure_reading_order.py` uses, so the question
is settled by tau and adjacency rather than by the theory being plausible.

    PYTHONPATH=. .venv/Scripts/python.exe scripts/experiment_region_order.py

The theory under test: on `wiki_ar.pdf` p6 the three caption lines fall in NO
detected region while the body lines around them all fall in one, so reading
each region contiguously should stop the caption interleaving — WITHOUT needing
a Caption label, which the model does not emit for that page.

The known risk: region coverage is incomplete. Line 25 of that page is body
text and is also orphaned, so any rule that treats "no region" as "a float"
will move it wrongly. That is exactly what the numbers are for.
"""

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
for channel in (sys.stdout, sys.stderr):
    if hasattr(channel, "reconfigure"):
        channel.reconfigure(encoding="utf-8")

from docyx.analysis.detectors.doclaynet import DocLayNetDetector
from docyx.analysis.layout import LayoutAnalyzer
from docyx.analysis.reading_order import ORDERABLE_TYPES, ReadingOrderCalculator
from docyx.pdf.renderer import PDFRenderer
from docyx.pipeline.extractor import DocyxPipeline

sys.path.insert(0, "scripts")
from measure_reading_order import adjacent_accuracy, kendall_tau  # noqa: E402

TRUTH_DIR = Path(".corpus/truth")


def naive(page):
    text = [el for el in page.elements if el.type in ORDERABLE_TYPES]
    return sorted(text, key=lambda el: (el.geometry.bbox.y, el.geometry.bbox.x))


def region_order(lines, regions):
    """Order regions as blocks, then order the lines inside each.

    Orphans — lines inside no region — are placed as blocks of their own, so a
    float is separated from the body it interleaves with. A line the model
    simply missed is treated the same way, which is the failure mode to watch.
    """
    groups = []
    for region in regions:
        box = region.geometry.bbox
        inside = [
            el
            for el in lines
            if box.contains(
                el.geometry.bbox.x + el.geometry.bbox.width / 2,
                el.geometry.bbox.y + el.geometry.bbox.height / 2,
            )
        ]
        if inside:
            groups.append(inside)

    claimed = {id(el) for group in groups for el in group}
    groups.extend([el] for el in lines if id(el) not in claimed)

    # Order the groups by their own geometry, using each group's bounding box
    # as a stand-in element, then expand.
    def key(group):
        return (
            min(el.geometry.bbox.y for el in group),
            min(el.geometry.bbox.x for el in group),
        )

    ordered = []
    for group in sorted(groups, key=key):
        ordered.extend(ReadingOrderCalculator.calculate(list(group)))
    return ordered


def score(order_indices, truth_order):
    truth_rank = {line: i for i, line in enumerate(truth_order)}
    pred_rank = {line: i for i, line in enumerate(order_indices)}
    shared = [line for line in truth_order if line in pred_rank]
    if len(shared) < 2:
        return 1.0, 1.0
    return (
        kendall_tau({k: truth_rank[k] for k in shared}, pred_rank),
        adjacent_accuracy(shared, pred_rank),
    )


def main() -> int:
    detector = DocLayNetDetector()
    analyzer = LayoutAnalyzer(detector=detector)
    pipeline = DocyxPipeline()

    print(f"{'page':<26}{'current':>16}{'region-grouped':>20}")
    print(f"{'':<26}{'tau':>8}{'adj':>8}{'tau':>10}{'adj':>10}")
    deltas = []
    for truth_file in sorted(TRUTH_DIR.glob("*.json")):
        truth = json.loads(truth_file.read_text(encoding="utf-8"))
        pdf = Path(".corpus") / truth["document"]
        if not pdf.exists():
            continue
        page_num = truth["page"]

        page = pipeline.process(str(pdf), "x", pages=[page_num]).pages[0]
        lines = naive(page)
        index = {id(el): i for i, el in enumerate(lines)}

        current = [index[id(el)] for el in ReadingOrderCalculator.calculate(list(lines))
                   if id(el) in index]

        renderer = PDFRenderer(str(pdf))
        regions = analyzer.analyze(renderer.render_page(page_num), page_num=page_num)
        renderer.close()
        grouped = [index[id(el)] for el in region_order(lines, regions) if id(el) in index]

        t0, a0 = score(current, truth["order"])
        t1, a1 = score(grouped, truth["order"])
        deltas.append((t1 - t0, a1 - a0))
        flag = "  <-- worse" if t1 < t0 - 1e-9 else ("  <-- better" if t1 > t0 + 1e-9 else "")
        name = f"{truth['document'][:-4]}.p{page_num}"
        print(f"{name:<26}{t0:>8.3f}{a0:>8.3f}{t1:>10.3f}{a1:>10.3f}{flag}")

    if deltas:
        print()
        print(f"mean delta: tau {sum(d[0] for d in deltas)/len(deltas):+.3f}  "
              f"adj {sum(d[1] for d in deltas)/len(deltas):+.3f}")
        print("A positive mean is NOT enough — a change that wins on floats and")
        print("loses on clean pages is a bad trade, so read the per-page column.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
