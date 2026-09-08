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

No model weights ship with the project. Layout and table detection are
injected through the `detector` seam, so **whichever model you plug in brings
its own licence** — that is a separate audit, and it has not been done, because
no model is wired up yet. §27's warning applies squarely there: an
open-source model does not imply open-source weights or commercial-use rights.

Evaluation datasets (DocLayNet, PubLayNet, PubTables-1M) also carry their own
terms and are not audited here; none are used yet.

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
