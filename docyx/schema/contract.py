"""The published schema contract.

§15 calls the page/element schema authoritative and §19 promises that minor
versions stay backward compatible. Neither is enforceable while the schema
exists only as Python classes, so it is also emitted as a JSON Schema file
that ships with the repo and is diffed by a test.

Regenerate after an intentional schema change:

    python -m docyx.schema.contract --write
"""

import json
from pathlib import Path
from typing import Any, Dict

from docyx.schema.models import Document

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schema"


def schema_version() -> str:
    return Document.model_fields["schema_version"].default


def schema_path() -> Path:
    return SCHEMA_DIR / f"v{schema_version()}.json"


def current_schema() -> Dict[str, Any]:
    return Document.model_json_schema()


def published_schema() -> Dict[str, Any]:
    return json.loads(schema_path().read_text(encoding="utf-8"))


def write() -> Path:
    path = schema_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current_schema(), indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    print(f"wrote {write()}")
