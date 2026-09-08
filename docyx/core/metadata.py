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
    source: ProvenanceSource
    engine: Optional[str] = None
    version: Optional[str] = None
    raw_confidence: Optional[float] = None
