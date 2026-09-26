"""Adapter for the assignment's structured fixtures.

Fixture metadata is supplied evidence, not a claim that an image was inspected.
The adapter only reshapes it into the normalized evidence payload that every
other source (PDF text, OCR, model-assisted extraction) also produces; the
``source`` field is provenance for the trace and never changes a policy rule.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from claims.policy import load_policy as _load_canonical_policy


def normalize_fixture(case: dict[str, Any]) -> dict[str, Any]:
    """Return claim input independent of expected outcomes and case identifiers."""
    source = case.get("input", case)
    claim = deepcopy(source)
    normalized = []
    for document in claim.get("documents", []):
        content = document.get("content") or document.get("fields") or {}
        item = {
            "file_id": document.get("file_id"),
            "file_name": document.get("file_name"),
            "doc_type": document.get("doc_type") or document.get("actual_type"),
            "quality": document.get("quality", "GOOD"),
            "fields": deepcopy(content),
            "patient_name": document.get("patient_name_on_doc") or content.get("patient_name"),
            "source": "fixture_metadata",
        }
        normalized.append(item)
    claim["documents"] = normalized
    # Fault injection is an evaluator concern; never forward this fixture-only
    # metadata into the normalized claim payload consumed by application code.
    claim.pop("simulate_component_failure", None)
    claim.pop("case_id", None)
    claim.pop("expected", None)
    return claim


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)["test_cases"]


def load_policy(path: str | Path) -> dict[str, Any]:
    """Validate and normalize a policy file into the canonical engine config."""
    return _load_canonical_policy(path)
