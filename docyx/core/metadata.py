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
    """Where an element's content came from, and whether a human changed it.

    `source` is the ORIGINAL producer and never changes on edit:
    `modified_by_user` marks a machine value a human corrected, while
    `source=manual` is reserved for elements a human created from nothing.
    """

    source: ProvenanceSource
    engine: Optional[str] = None
    version: Optional[str] = None
    raw_confidence: Optional[float] = None
    #: A human changed this element's value after extraction.
    modified_by_user: bool = False
    #: What the machine said, kept so an edit is never destructive.
    original_text: Optional[str] = None
