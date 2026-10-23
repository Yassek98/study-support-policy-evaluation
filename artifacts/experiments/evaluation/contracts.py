"""Closed-world JSON-Schema validation helpers for WP6 artifacts."""
from __future__ import annotations

import copy
import json
import pathlib
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


SCHEMAS_DIR = pathlib.Path(__file__).resolve().parents[1] / "schemas"


@lru_cache(maxsize=1)
def _schema_registry() -> Registry:
    resources: list[tuple[str, Resource]] = []
    for path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema)
        resources.append((path.resolve().as_uri(), resource))
        resources.append((f"https://local.thesis.invalid/schemas/{path.name}", resource))
        if schema.get("$id"):
            resources.append((schema["$id"], resource))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def schema_validator(schema_filename: str) -> Draft202012Validator:
    path = SCHEMAS_DIR / schema_filename
    if not path.is_file():
        raise FileNotFoundError(f"Unknown schema: {path}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    if "$id" not in schema:
        schema = copy.deepcopy(schema)
        schema["$id"] = path.resolve().as_uri()
    return Draft202012Validator(
        schema,
        registry=_schema_registry(),
        format_checker=FormatChecker(),
    )


def validation_errors(instance: Any, schema_filename: str) -> list[str]:
    """Return deterministic, human-readable validation errors."""
    validator = schema_validator(schema_filename)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    formatted: list[str] = []
    for error in errors:
        path = ".".join(str(part) for part in error.absolute_path) or "$"
        formatted.append(f"{path}: {error.message}")
    return formatted


def require_valid(instance: Any, schema_filename: str, label: str) -> None:
    errors = validation_errors(instance, schema_filename)
    if errors:
        raise ValueError(f"{label} violates {schema_filename}: " + "; ".join(errors))
