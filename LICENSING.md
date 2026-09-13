# Licensing

**Docyx is licensed under AGPL-3.0** (see `LICENSE`).

This document is the dependency and licence inventory backend plan §27 requires
before any commercial distribution, plus the reasoning behind the licence
choice, so it can be revisited deliberately rather than rediscovered.

## Dependency inventory

| Dependency | Version | Licence | Role | Notes |
|---|---|---|---|---|
| PyMuPDF | >=1.23 | **AGPL-3.0 or Artifex commercial** | Text extraction, rendering | The binding constraint. See below. |
| pydantic | >=2.0 | MIT | Schema models | |
| opencv-python-headless | >=4.8 | Apache-2.0 | Visual element detection | |
| numpy | >=1.24 | BSD-3-Clause | Array handling for OpenCV | |

### Optional model stack (`requirements-models.txt`)

| Dependency | Licence | Role |
|---|---|---|
| torch | BSD-3-Clause | Inference runtime (CPU) |
| transformers | Apache-2.0 | Model loading and pre/post-processing |
| timm | Apache-2.0 | Table Transformer's backbone |
| pillow | MIT-CMU | Image handling |
| `microsoft/table-transformer-*` weights | **MIT** | Table detection and structure |

All permissive and compatible with AGPL-3.0. Not required by the core: the
pipeline falls back to heuristics without them.

No model weights ship with the project. Layout and table detection are injected
through the `detector` seam, so **whichever model you plug in brings its own
licence**. §27's warning applies squarely: an open-source model does not imply
open-source weights or commercial-use rights.

### Weights audit — one model is now wired up

`TableTransformerDetector` (commit `2dd28d1`) downloads weights at first use.
They are **not** vendored, so this is a licence obligation on the *deployer*,
not on this repository:

| Weights | Licence | Downloaded by |
|---|---|---|
| `microsoft/table-transformer-detection` | MIT | `TableTransformerDetector` on first `detect()` |
| `microsoft/table-transformer-structure-recognition` | MIT | same |

MIT permits commercial use and imposes only attribution, so neither constrains
the delivery model. Nothing else is wired up; **re-run this audit whenever a
detector is added**, because a permissively licensed *library* routinely ships
non-commercial weights (several layout models are CC-BY-NC), and that asymmetry
is what §27 warns about.

Note the training data is a separate question again: PubTables-1M is CC-BY 4.0,
but a model's weights licence does not inherit its dataset's terms, and vendors
do not always say so.

Evaluation datasets (DocLayNet CDLA-Permissive-1.0, PubLayNet CDLA-Permissive-1.0,
PubTables-1M CC-BY 4.0) carry their own terms. None are used yet; Phase 4
criterion 1 will introduce them, and they are permissive for research and
commercial use but require attribution.

## Why AGPL-3.0

PyMuPDF is dual-licensed AGPL-3.0 or commercial (Artifex). AGPL is viral across
distribution, so a project linking it either adopts a compatible copyleft
licence or buys the commercial licence.

Adopting AGPL was chosen over swapping the library because:

1. **It matches the stated positioning.** §25 names open-source, local and
   self-hosted operation as the differentiator. AGPL expresses that rather than
   fighting it.
2. **Swapping costs extraction quality now, for a decision deferred to later.**
   PyMuPDF exposes span-level font flags, colour, and the block/line/span
   structure the line-granularity extractor depends on. `pdfminer.six` and
   `pypdfium2` give less: bold and italic would have to be inferred from font
   names. §24 explicitly calls the delivery model "a later decision, not a v1
   requirement" — paying a quality cost now to keep a deferred option open is
   the wrong trade.
3. **The escape hatch is purchasable.** An Artifex commercial licence removes
   the constraint without touching code. That is a business decision.
4. **The code cost of reversing is small and has been kept small.** PyMuPDF is
   confined to `docyx/pdf/`; everything outside depends on the protocols in
   `docyx/pdf/protocols.py`. A backend swap is a change to one package.

## What this decision forecloses

AGPL's network clause means **anyone you offer this to over a network can
demand the source**, including their own modifications. Concretely, this rules
out:

- A proprietary hosted API built on Docyx.
- Proprietary enterprise or private deployments where the customer embeds
  Docyx in closed software.

Both appear in §24's "future commercialization" sketch. **If either becomes a
real plan, this decision must be revisited** — by buying the Artifex commercial
licence, or by replacing PyMuPDF with a permissive stack
(`pypdfium2` for rendering, `pdfplumber`/`pdfminer.six` for text) and
re-licensing Docyx permissively.

Revisit it *before* accepting outside contributions. While the project is
single-author it can be relicensed freely; once others hold copyright in it,
relicensing needs every contributor's agreement.
