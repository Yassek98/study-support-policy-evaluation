"""Loader for the closed ten-family scenario sampling frame."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from .context import ValidationContext
from .report import ValidationReport


def load_scenario_family_taxonomy(repo_root: Path) -> dict:
    taxonomy_path = repo_root / "taxonomy" / "SCENARIO_FAMILY_TAXONOMY.v0.1.0.json"
    schema_path = repo_root / "schemas" / "scenario_family_taxonomy.schema.json"
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(taxonomy, schema)
    family_ids = [item["family_id"] for item in taxonomy["families"]]
    if len(family_ids) != len(set(family_ids)) or set(family_ids) != {
        f"F{index:02d}" for index in range(1, 11)
    }:
        raise ValueError("Scenario taxonomy must define each canonical family exactly once")
    scenario_ids = set()
    # Development validation must not open future held-out material or require a
    # mutation of the frozen development-assignment table when it is authored.
    for path in sorted((repo_root / "scenarios" / "development").rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        scenario_id = data.get("scenario_id")
        if scenario_id in scenario_ids:
            raise ValueError(f"Duplicate scenario_id in sampling frame: {scenario_id}")
        scenario_ids.add(scenario_id)
    assigned = set(taxonomy["assignments"])
    if assigned != scenario_ids:
        raise ValueError(
            f"Scenario taxonomy coverage mismatch: missing={sorted(scenario_ids-assigned)}, "
            f"unexpected={sorted(assigned-scenario_ids)}"
        )
    return taxonomy


def check_scenario_family_taxonomy(
    ctx: ValidationContext, report: ValidationReport
) -> None:
    """Validate the closed family frame and exhaustive scenario assignment."""
    try:
        taxonomy = load_scenario_family_taxonomy(ctx.paths.base)
    except Exception as exc:
        report.error(f"Controlled scenario-family taxonomy invalid: {exc}")
        return
    report.ok(
        "Controlled scenario-family taxonomy valid: "
        f"{len(taxonomy['families'])} families, "
        f"{len(taxonomy['assignments'])} uniquely assigned scenarios"
    )
