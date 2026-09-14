from enum import Enum
from typing import Optional
from pydantic import BaseModel


class ConfidenceType(str, Enum):
    EXACT = "exact"
    DETECTED = "detected"
    INFERRED = "inferred"


class Confidence(BaseModel):
    value: float
    type: ConfidenceType


class ProvenanceSource(str, Enum):
    NATIVE_PDF = "native_pdf"
    LAYOUT_MODEL = "layout_model"
    TABLE_MODEL = "table_model"
    GEOMETRY_INFERENCE = "geometry_inference"
    MANUAL = "manual"
    OCR = "ocr"
    VISUAL_INFERENCE = "visual_inference"


class Provenance(BaseModel):
    """Where an element's content came from, and whether a human overrode it.

    ``source`` is the ORIGINAL producer and does not change when a human edits
    the value. "Where did this come from" is a fact about history, and losing
    it would make an edited element indistinguishable from one typed from
    scratch — which is the distinction an annotation workflow is built on:

    * ``modified_by_user`` — a machine produced this and a human corrected it.
      ``source`` still names the machine; ``original_text`` keeps what it said.
    * ``source=manual`` — a human created this element from nothing, by drawing
      a box on a page the machine could not read. Nothing produces it yet; it
      arrives with the workspace UI (§11).

    Only ``original_text`` is preserved, because text is the only value §11
    specifies as editable-with-history. Bounding-box editing (§10) sets
    ``modified_by_user`` without keeping the original geometry; add
    ``original_geometry`` when something actually needs to undo across a
    session rather than within one.
    """

    source: ProvenanceSource
    engine: Optional[str] = None
    version: Optional[str] = None
    raw_confidence: Optional[float] = None
    #: A human changed this element's value after extraction.
    modified_by_user: bool = False
    #: What the machine said, kept so an edit is never destructive.
    original_text: Optional[str] = None
