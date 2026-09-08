import pytest
from pydantic import ValidationError
from docyx.core.metadata import (
    Confidence,
    ConfidenceType,
    Provenance,
    ProvenanceSource,
)


def test_confidence_valid():
    conf = Confidence(value=1.0, type=ConfidenceType.EXACT)
    assert conf.value == 1.0
    assert conf.type == ConfidenceType.EXACT
    assert conf.type == "exact"

    conf_str = Confidence(value=0.85, type="detected")
    assert conf_str.type == ConfidenceType.DETECTED


def test_confidence_invalid_type():
    with pytest.raises(ValidationError):
        Confidence(value=0.5, type="invalid_type")


def test_provenance_minimal():
    prov = Provenance(source=ProvenanceSource.NATIVE_PDF)
    assert prov.source == ProvenanceSource.NATIVE_PDF
    assert prov.source == "native_pdf"
    assert prov.engine is None
    assert prov.version is None
    assert prov.raw_confidence is None


def test_provenance_full():
    prov = Provenance(
        source=ProvenanceSource.LAYOUT_MODEL,
        engine="DocLayNet-YOLOv8",
        version="1.0.0",
        raw_confidence=0.92,
    )
    assert prov.source == ProvenanceSource.LAYOUT_MODEL
    assert prov.engine == "DocLayNet-YOLOv8"
    assert prov.version == "1.0.0"
    assert prov.raw_confidence == 0.92


def test_provenance_invalid_source():
    with pytest.raises(ValidationError):
        Provenance(source="unknown_source")
