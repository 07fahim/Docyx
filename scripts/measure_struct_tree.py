"""Measure how many PDFs in a corpus carry a usable structure tree.

Backend plan §10 lists "use reliable native PDF structure when available" as
step 1 of the reading-order strategy, and nothing implements it. A tagged PDF
(/StructTreeRoot) carries real reading order, semantic region types, table
row/column roles and figure alt text — exactly the things the vision models are
currently approximating. Whether that is worth a phase depends entirely on how
common tagged PDFs are in *your* corpus, which is an empirical question.

This answers it. Point it at a directory of representative PDFs:

    python scripts/measure_struct_tree.py path/to/corpus --json report.json

Reports, per file: whether a structure tree exists, whether the document is
marked, the declared /Lang, and the distribution of structure element types.
The type histogram matters as much as the percentage — a tree containing only
/Document and /P is far less useful than one with /H1../H6, /Table and /Figure.

Uses PyMuPDF's raw object access only, so it needs no dependency beyond what
the pipeline already has.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

import fitz

# Structure types that actually buy us something the vision path cannot do
# cheaply or exactly.
HIGH_VALUE_TYPES = {
    "H1", "H2", "H3", "H4", "H5", "H6", "H",
    "Table", "TR", "TH", "TD",
    "Figure", "Formula", "Caption", "L", "LI", "TOC",
}


def _resolve(doc: fitz.Document, ref: str) -> Optional[int]:
    """Turn a '12 0 R' indirect reference into an xref number."""
    parts = ref.split()
    if len(parts) >= 3 and parts[-1] == "R":
        try:
            return int(parts[0])
        except ValueError:
            return None
    return None


def _get(doc: fitz.Document, xref: int, key: str) -> Optional[str]:
    kind, value = doc.xref_get_key(xref, key)
    return None if kind == "null" else value


def _get_resolved(doc: fitz.Document, xref: int, key: str) -> Optional[str]:
    """Like _get, but follows one level of indirection.

    Real files routinely store /Lang as an indirect reference, which _get
    returns verbatim as '2396 0 R' rather than the string it points at.
    """
    value = _get(doc, xref, key)
    if value is None:
        return None
    target = _resolve(doc, value)
    if target is None:
        return value
    resolved = doc.xref_object(target, compressed=True)
    return resolved.strip().lstrip("(").rstrip(")").strip() or None


def _walk_types(doc: fitz.Document, xref: int, counter: Counter, depth: int = 0, seen=None):
    """Collect /S structure types by walking /K children."""
    if depth > 40:  # cycle / pathological nesting guard
        return
    seen = seen if seen is not None else set()
    if xref in seen:
        return
    seen.add(xref)

    s_type = _get(doc, xref, "S")
    if s_type:
        counter[s_type.lstrip("/")] += 1

    kids = _get(doc, xref, "K")
    if not kids:
        return

    # /K is either one reference, an array of them, or inline content refs we
    # do not care about here.
    for token in kids.replace("[", " ").replace("]", " ").split("R"):
        child = _resolve(doc, token.strip() + " R") if token.strip() else None
        if child:
            _walk_types(doc, child, counter, depth + 1, seen)


def inspect(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {"file": str(path), "pages": 0, "tagged": False, "error": None}
    try:
        doc = fitz.open(path)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    try:
        result["pages"] = doc.page_count
        catalog = doc.pdf_catalog()
        keys = doc.xref_get_keys(catalog)

        result["marked"] = False
        if "MarkInfo" in keys:
            mark_ref = _get(doc, catalog, "MarkInfo") or ""
            mark_xref = _resolve(doc, mark_ref)
            marked = (
                _get(doc, mark_xref, "Marked")
                if mark_xref
                else ("true" if "/Marked true" in mark_ref else None)
            )
            result["marked"] = str(marked).lower() == "true"

        result["lang"] = _get_resolved(doc, catalog, "Lang")

        types: Counter = Counter()
        if "StructTreeRoot" in keys:
            root_xref = _resolve(doc, _get(doc, catalog, "StructTreeRoot") or "")
            if root_xref:
                result["tagged"] = True
                _walk_types(doc, root_xref, types)

        result["struct_types"] = dict(types.most_common())
        result["high_value_types"] = sorted(set(types) & HIGH_VALUE_TYPES)
        result["usable"] = bool(result["high_value_types"])
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        doc.close()
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", type=Path, help="directory of PDFs (searched recursively)")
    ap.add_argument("--json", type=Path, help="write the full per-file report here")
    args = ap.parse_args()

    if not args.corpus.exists():
        print(f"No such path: {args.corpus}", file=sys.stderr)
        return 2

    pdfs = sorted(args.corpus.rglob("*.pdf")) if args.corpus.is_dir() else [args.corpus]
    if not pdfs:
        print(f"No PDFs found under {args.corpus}", file=sys.stderr)
        return 2

    results = [inspect(p) for p in pdfs]
    ok = [r for r in results if not r["error"]]
    tagged = [r for r in ok if r["tagged"]]
    usable = [r for r in ok if r.get("usable")]
    with_lang = [r for r in ok if r.get("lang")]

    total = len(ok) or 1
    print(f"\nCorpus: {args.corpus}")
    print(f"  readable PDFs        {len(ok)} of {len(results)}")
    print(f"  tagged (/StructTreeRoot)  {len(tagged):4}  {len(tagged)/total:6.1%}")
    print(f"  with useful tag types     {len(usable):4}  {len(usable)/total:6.1%}   <- the number that matters")
    print(f"  declaring /Lang           {len(with_lang):4}  {len(with_lang)/total:6.1%}")

    all_types: Counter = Counter()
    for r in ok:
        all_types.update(r.get("struct_types", {}))
    if all_types:
        print("\n  structure types seen:")
        for name, count in all_types.most_common(20):
            marker = " *" if name in HIGH_VALUE_TYPES else ""
            print(f"    {name:<14} {count:6}{marker}")
        print("    (* = gives us something the vision path cannot do exactly)")

    failed = [r for r in results if r["error"]]
    if failed:
        print(f"\n  unreadable: {len(failed)}")
        for r in failed[:5]:
            print(f"    {Path(r['file']).name}: {r['error']}")

    print(
        "\nRead this as: if 'with useful tag types' is high, the struct-tree path\n"
        "gives exact reading order and table structure on that fraction of the\n"
        "corpus for far less work than improving the vision models.\n"
    )

    if args.json:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Full report: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
