"""Compare Docyx and Docling reading order against hand-labelled ground truth.

    python scripts/compare_tools.py

Prerequisite: .venv-bench/Scripts/python.exe scripts/docling_dump.py <pdf>

The two tools segment a page differently — Docyx emits lines, Docling emits
paragraph-ish blocks — so their outputs cannot be matched item to item. Instead
each tool's page output is flattened, in its own reading order, to a single
stream of [a-z0-9] characters with everything else discarded and no separator.

That normalisation is what makes the comparison fair:

- segmentation stops mattering (lines vs blocks collapse to the same stream)
- hyphenation stops mattering ("representa-" + "tion" and "representation"
  both become "representation")
- what survives is the ORDER the characters are emitted in, which is the thing
  under test

Scores are coverage of the ground-truth stream, which is the page's own
lines in the hand-labelled order.

Two biases had to be removed before the numbers meant anything, both of which
flattered Docyx (see `stream` and `coverage`): ligature folding, and scoring
recall instead of symmetric similarity so page-spanning blocks aren't charged
as ordering errors. Together they accounted for the entire apparent gap.

Honest limit that remains: the reference stream is built from Docyx's own
characters, so the *content* side of the comparison is Docyx's by construction.
This measures ORDER, not extraction fidelity. A tool that reads different
characters than PyMuPDF cannot be graded fairly here — the `chars` columns
exist to make that visible rather than invisible.
"""

import json
import re
import unicodedata
import sys
from difflib import SequenceMatcher
from pathlib import Path

from docyx.analysis.reading_order import ORDERABLE_TYPES
from docyx.pipeline.extractor import DocyxPipeline
from scripts.measure_reading_order import TRUTH_DIR, CORPUS_DIR, load_truth, neutral_lines

DOCLING_DIR = Path(".corpus/docling")
KEEP = re.compile(r"[^a-z0-9]")


def stream(texts) -> str:
    """Flatten to a comparable character sequence (see module docstring).

    NFKD first, so a typographic ligature folds to its letters. Without it the
    comparison is silently biased: Docyx preserves 'ﬁ' (U+FB01) exactly as the
    file encodes it, Docling expands it to 'fi', and a plain [a-z0-9] filter
    drops the former while keeping the latter — inventing a character deficit
    for the tool that was more faithful, and penalising the other for agreeing
    with it. The BERT page alone has enough 'ﬁne'/'speciﬁc'/'ﬁnal' to move the
    score by a third of a percent.
    """
    folded = unicodedata.normalize("NFKD", "".join(texts).lower())
    return KEEP.sub("", folded)


def coverage(reference: str, candidate: str) -> float:
    """How much of the ground-truth sequence the tool emits, in the right order.

    Recall of the reference, NOT symmetric similarity. Extra material in the
    candidate must not count against it: Docling emits paragraph blocks that
    span page boundaries, so scoring page 0 sees a block whose tail genuinely
    belongs to page 1. A symmetric ratio charged that overhang as an ordering
    error — 310 characters of it on the BERT title page — which measured our
    page-slicing convention rather than either tool's reading order.

    Under-extraction is still punished, which is the property we want.
    """
    if not reference:
        return 0.0
    matcher = SequenceMatcher(None, reference, candidate, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks()) / len(reference)


def compare(truth: dict) -> dict | None:
    doc = DocyxPipeline().process(str(CORPUS_DIR / truth["document"]), truth["document"])
    page = doc.pages[truth["page"]]
    lines = neutral_lines(page)

    reference = stream(lines[i].text for i in truth["order"])

    docyx_order = sorted(
        (el for el in page.elements if el.type in ORDERABLE_TYPES),
        key=lambda el: el.reading_order,
    )
    docyx_stream = stream(el.text for el in docyx_order)

    dump = DOCLING_DIR / f"{Path(truth['document']).stem}.json"
    if not dump.exists():
        print(f"  (no Docling dump for {truth['document']}; skipping that column)")
        docling_stream = None
    else:
        items = json.loads(dump.read_text(encoding="utf-8"))
        docling_stream = stream(i["text"] for i in items if i["page"] == truth["page"])

    return {
        "page": f"{Path(truth['document']).stem} p{truth['page']}",
        "ref_chars": len(reference),
        "docyx": coverage(reference, docyx_stream),
        "docyx_chars": len(docyx_stream),
        "docling": coverage(reference, docling_stream) if docling_stream is not None else None,
        "docling_chars": len(docling_stream) if docling_stream is not None else None,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    results = [compare(load_truth(p)) for p in sorted(TRUTH_DIR.glob("*.json"))]

    header = f"{'page':22s} {'ref':>6s} {'docyx':>7s} {'chars':>6s} {'docling':>8s} {'chars':>6s}"
    print(header)
    for r in results:
        docling = f"{r['docling']:8.3f}" if r["docling"] is not None else f"{'--':>8s}"
        dchars = f"{r['docling_chars']:6d}" if r["docling_chars"] is not None else f"{'--':>6s}"
        print(
            f"{r['page']:22s} {r['ref_chars']:6d} {r['docyx']:7.3f} "
            f"{r['docyx_chars']:6d} {docling} {dchars}"
        )

    scored = [r for r in results if r["docling"] is not None]
    if scored:
        print()
        print(f"mean docyx   {sum(r['docyx'] for r in scored) / len(scored):.3f}")
        print(f"mean docling {sum(r['docling'] for r in scored) / len(scored):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
