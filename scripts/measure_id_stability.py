"""Are element ids a usable key for reattaching saved edits?

The workspace can export corrections but cannot load them back, because a
resume has to match elements by `id` across a fresh extraction and nothing
yet says those are stable. Ids are positional -- `page1_b0_l6` is block 0,
line 6 of PyMuPDF's enumeration -- so the answer is not obvious in either
direction, and reasoning about it is not evidence.

Four questions, each a separate failure mode for resume:

1. UNIQUE      -- can one id name two elements? Then a reattach is ambiguous.
2. DETERMINISM -- same file, same code, twice. Then a same-session round trip works.
3. SUBSET      -- pages=[n] (what the workspace runs) against a whole-document
                  run (what the CLI runs). Then an edit made in the viewer can
                  be reattached to a batch export.
4. DETECTORS   -- do native text ids move when a layout/table/visual detector
                  is switched on? Then enabling a model does not orphan edits.

5. `--against REF` answers the one the other four cannot: do ids survive a
   change to the extraction code? It builds a worktree at REF, extracts the
   same page there, and diffs. Run it before shipping an extraction change to
   see how many saved edits it would orphan.

    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_id_stability.py
    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_id_stability.py .corpus/wiki_ar.pdf
    PYTHONPATH=. .venv/Scripts/python.exe scripts/measure_id_stability.py --against 4b9b461
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

from docyx.analysis.layout import LayoutAnalyzer
from docyx.analysis.tables import TableAnalyzer
from docyx.analysis.visual import VisualAnalyzer
from docyx.pipeline.extractor import DocyxPipeline

#: Run inside the worktree, where this repo's newer helpers do not exist. The
#: `pages` kwarg postdates some of the commits worth comparing against.
DUMPER = """
import hashlib, json, sys
from docyx.pipeline.extractor import DocyxPipeline
try:
    doc = DocyxPipeline().process(sys.argv[1], "x", pages=[0])
except TypeError:
    doc = DocyxPipeline().process(sys.argv[1], "x")
page = doc.pages[0]
out, stack = {}, list(page.elements) + list(page.diagnostic_elements)
while stack:
    e = stack.pop()
    out[e.id] = hashlib.sha1((e.text or e.type).encode()).hexdigest()[:12]
    stack.extend(getattr(e, "children", []))
json.dump(out, open(sys.argv[2], "w"))
"""

CORPUS = Path(".corpus")
#: Enough files to cover the genres, few enough to finish in a coffee break.
DEFAULT = ["arxiv_attention.pdf", "wiki_ar.pdf", "word_bn.pdf", "irs_fw9.pdf"]
PAGES = [0, 1]


def fingerprint(page) -> Dict[str, str]:
    """id -> hash of what the id currently names.

    The pair is the point: an id that survives while its text changes is worse
    than an id that disappears, because a reattach then silently moves a human
    correction onto a different line.
    """
    out: Dict[str, str] = {}
    stack = list(page.elements) + list(page.diagnostic_elements)
    while stack:
        element = stack.pop()
        digest = hashlib.sha1((element.text or element.type).encode("utf-8"))
        out[element.id] = digest.hexdigest()[:12]
        stack.extend(element.children)
    return out


def duplicates(page) -> List[Tuple[str, int]]:
    seen: Counter = Counter()
    stack = list(page.elements) + list(page.diagnostic_elements)
    while stack:
        element = stack.pop()
        seen[element.id] += 1
        stack.extend(element.children)
    return [(i, n) for i, n in seen.items() if n > 1]


def compare(left: Dict[str, str], right: Dict[str, str]) -> Dict[str, int]:
    """How a saved edit map would fare against a later extraction."""
    common = left.keys() & right.keys()
    return {
        "kept": len(common),
        "lost": len(left.keys() - right.keys()),
        "new": len(right.keys() - left.keys()),
        # The dangerous one: the id survived but now names something else.
        "moved": sum(1 for k in common if left[k] != right[k]),
    }


def run(path: Path, pipeline: DocyxPipeline, pages) -> Dict[int, object]:
    document = pipeline.process(str(path), path.stem, pages=pages)
    return {p.page_number - 1: p for p in document.pages}


def verdict(name: str, results: List[Dict[str, int]]) -> bool:
    lost = sum(r["lost"] for r in results)
    moved = sum(r["moved"] for r in results)
    kept = sum(r["kept"] for r in results)
    ok = lost == 0 and moved == 0
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<34} "
          f"kept {kept:>5}  lost {lost:>4}  moved {moved:>4}")
    return ok


def against(ref: str, files: List[Path]) -> int:
    """Extract page 0 at REF and at HEAD, and diff the id -> content map."""
    scratch = Path(tempfile.mkdtemp(prefix="docyx_id_"))
    tree = scratch / "tree"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(tree), ref], check=True)
    script = scratch / "dump.py"
    script.write_text(DUMPER, encoding="utf-8")
    try:
        print(f"\n{ref} -> HEAD\n")
        worst = 0
        for path in files:
            old, new = scratch / "old.json", scratch / "new.json"
            for target, root in ((old, str(tree)), (new, str(Path.cwd()))):
                subprocess.run([sys.executable, str(script), str(path.resolve()), str(target)],
                               env={**os.environ, "PYTHONPATH": root}, check=True)
            a, b = (json.loads(p.read_text()) for p in (old, new))
            common = a.keys() & b.keys()
            moved = sum(1 for k in common if a[k] != b[k])
            lost = len(a.keys() - b.keys())
            print(f"  {path.name:<26} {len(a):>4} -> {len(b):<4} ids   "
                  f"kept {len(common):>4}  lost {lost:>4}  new {len(b.keys() - a.keys()):>4}  "
                  f"moved {moved:>3}")
            worst = max(worst, lost + moved)
        print("\n  'moved' is the one that matters: the id survived a code change"
              "\n  but now names different content, so a reattach lands on the wrong line.")
        return 0 if worst == 0 else 1
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)], check=False)


def main(argv: List[str]) -> int:
    if "--against" in argv:
        at = argv.index("--against")
        ref = argv[at + 1]
        rest = argv[:at] + argv[at + 2:]
        files = [Path(a) for a in rest] or [CORPUS / n for n in DEFAULT]
        return against(ref, [f for f in files if f.exists()])

    files = [Path(a) for a in argv] or [CORPUS / n for n in DEFAULT]
    files = [f for f in files if f.exists()]
    if not files:
        print("no corpus files found")
        return 2

    plain = DocyxPipeline()
    with_models = DocyxPipeline(
        layout_analyzer=LayoutAnalyzer(),
        table_analyzer=TableAnalyzer(),
        visual_analyzer=VisualAnalyzer(),
    )

    unique, determinism, subset, detectors = [], [], [], []
    collisions = 0

    for path in files:
        print(f"\n{path.name}")
        pages = [p for p in PAGES]

        first = run(path, plain, pages)
        second = run(path, plain, pages)
        # The whole document, then look only at the pages we also ran alone.
        whole = run(path, plain, None)
        modelled = run(path, with_models, pages)

        for index in sorted(first):
            a = fingerprint(first[index])
            dupes = duplicates(first[index])
            collisions += len(dupes)
            unique.append({"kept": len(a), "lost": 0, "new": 0, "moved": len(dupes)})

            determinism.append(compare(a, fingerprint(second[index])))
            if index in whole:
                subset.append(compare(a, fingerprint(whole[index])))
            # Native text only: detected elements are new by construction, and
            # counting them as "new" would hide whether the text ids moved.
            native = {k: v for k, v in a.items() if "_layout_" not in k
                      and "_visual_" not in k and "_table_" not in k}
            after = fingerprint(modelled[index])
            detectors.append(compare(native, {k: v for k, v in after.items() if k in native}))

    print(f"\n{'=' * 64}\n{len(files)} files, pages {PAGES}\n")
    ok = [
        verdict("1. unique within a page", unique),
        verdict("2. deterministic across runs", determinism),
        verdict("3. subset run == whole-document run", subset),
        verdict("4. unmoved when detectors are added", detectors),
    ]
    print(f"\n  id collisions: {collisions}")
    print("\n  'moved' means the id survived but now names different content —"
          "\n  the failure that silently reattaches a correction to the wrong line.")
    print("\n  Not tested: stability across a change to the extraction code."
          "\n  That needs two commits, not two runs.")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
