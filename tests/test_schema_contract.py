"""Guards the published schema contract (§15, §19).

The point of these tests is that an accidental schema change fails loudly,
and an intentional one requires regenerating the artifact — which shows up
as a reviewable diff instead of silently shipping.
"""

import pytest

from docyx.schema.contract import current_schema, published_schema, schema_path, schema_version


def test_published_schema_matches_the_models():
    assert schema_path().exists(), (
        f"{schema_path()} is missing. Run: python -m docyx.schema.contract --write"
    )
    assert published_schema() == current_schema(), (
        "The models no longer match the published schema.\n"
        "If this change is intentional, regenerate the artifact and consider whether "
        "it needs a schema_version bump (§19: minor versions stay backward compatible):\n"
        "    python -m docyx.schema.contract --write"
    )


def test_artifact_filename_tracks_the_version():
    assert schema_path().name == f"v{schema_version()}.json"


@pytest.mark.parametrize(
    "pointer",
    [
        ("properties", "schema_version"),
        ("properties", "document_id"),
        ("properties", "pages"),
        ("$defs", "Page", "properties", "elements"),
        ("$defs", "Page", "properties", "diagnostic_elements"),
        ("$defs", "Page", "properties", "status"),
        ("$defs", "Element", "properties", "geometry"),
        ("$defs", "Element", "properties", "confidence"),
        ("$defs", "Element", "properties", "provenance"),
    ],
)
def test_load_bearing_fields_are_present(pointer):
    """These are named in the plan as the contract. Removing one is a major break."""
    node = published_schema()
    for key in pointer:
        assert key in node, f"missing {'.'.join(pointer)}"
        node = node[key]


def test_reserved_values_stay_reserved():
    """§16.1/§16.2: ocr, visual_inference and inferred are reserved for the OCR
    extension. They must exist in the schema now so adding OCR later is not a
    breaking change, and must stay unused in v1."""
    defs = published_schema()["$defs"]
    assert set(defs["ProvenanceSource"]["enum"]) >= {"ocr", "visual_inference"}
    assert "inferred" in defs["ConfidenceType"]["enum"]
