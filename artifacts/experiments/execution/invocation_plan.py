"""Deterministic planned-invocation inventory and coverage verification."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

try:
    from execution.run_record_validator import _get_compiled_validator
except ImportError:
    from artifacts.experiments.execution.run_record_validator import _get_compiled_validator


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def compute_plan_sha256(plan: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(plan))
    payload.pop("plan_sha256", None)
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def build_invocation_plan(
    *,
    batch_id: str,
    protocol_version: str,
    execution_mode: str,
    configuration_ids: Sequence[str],
    scenario_ids: Sequence[str],
    replication_count: int,
    schemas_dir,
) -> dict[str, Any]:
    """Create the complete Cartesian invocation inventory without choosing R."""
    if type(replication_count) is not int or replication_count < 1:
        raise ValueError("replication_count must be an explicitly supplied integer >= 1")
    if len(configuration_ids) != len(set(configuration_ids)):
        raise ValueError("configuration_ids contains duplicates")
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("scenario_ids contains duplicates")
    invocations = []
    for configuration_id in sorted(configuration_ids):
        for scenario_id in sorted(scenario_ids):
            for replication_index in range(1, replication_count + 1):
                run_id = (
                    f"{batch_id}.{configuration_id}.{scenario_id}.R{replication_index:04d}"
                )
                invocations.append(
                    {
                        "run_id": run_id,
                        "configuration_id": configuration_id,
                        "scenario_id": scenario_id,
                        "replication_index": replication_index,
                    }
                )
    plan = {
        "schema_version": "0.1.0",
        "batch_id": batch_id,
        "protocol_version": protocol_version,
        "execution_mode": execution_mode,
        "configuration_ids": sorted(configuration_ids),
        "scenario_ids": sorted(scenario_ids),
        "replication_count": replication_count,
        "invocation_count": len(invocations),
        "invocations": invocations,
    }
    plan["plan_sha256"] = compute_plan_sha256(plan)
    validator = _get_compiled_validator("invocation_plan.schema.json", str(schemas_dir))
    errors = list(validator.iter_errors(plan))
    if errors:
        raise ValueError(f"Invocation plan schema failure: {errors[0].message}")
    return plan


def verify_result_coverage(
    plan: Mapping[str, Any], results: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    """Reject duplicate/unplanned results and expose every missing invocation."""
    if compute_plan_sha256(plan) != plan.get("plan_sha256"):
        raise ValueError("Invocation plan hash mismatch")
    invocations = plan.get("invocations", [])
    if plan.get("invocation_count") != len(invocations):
        raise ValueError("Invocation plan invocation_count mismatch")
    replication_count = plan.get("replication_count")
    expected_replications = set(range(1, replication_count + 1))
    expected_cells = {
        (configuration_id, scenario_id)
        for configuration_id in plan.get("configuration_ids", [])
        for scenario_id in plan.get("scenario_ids", [])
    }
    expected_invocation_count = len(expected_cells) * replication_count
    if len(invocations) != expected_invocation_count:
        raise ValueError("Invocation plan does not cover its declared Cartesian frame")
    replications_by_cell: dict[tuple[str, str], set[int]] = {}
    for entry in invocations:
        expected_run_id = (
            f"{plan['batch_id']}.{entry['configuration_id']}."
            f"{entry['scenario_id']}.R{entry['replication_index']:04d}"
        )
        if entry["run_id"] != expected_run_id:
            raise ValueError(f"Invocation plan contains non-canonical run_id: {entry['run_id']}")
        cell = (entry["configuration_id"], entry["scenario_id"])
        replications_by_cell.setdefault(cell, set()).add(entry["replication_index"])
    incomplete_cells = {
        cell: sorted(expected_replications - replications)
        for cell, replications in replications_by_cell.items()
        if replications != expected_replications
    }
    if incomplete_cells:
        raise ValueError(f"Invocation plan has incomplete replication cells: {incomplete_cells}")
    if set(replications_by_cell) != expected_cells:
        raise ValueError("Invocation plan cell set differs from its declared Cartesian frame")
    expected = {entry["run_id"]: entry for entry in invocations}
    if len(expected) != len(invocations):
        raise ValueError("Invocation plan contains duplicate run_id values")
    observed: dict[str, Mapping[str, Any]] = {}
    for result in results:
        run_id = result.get("run_id")
        if run_id in observed:
            raise ValueError(f"Duplicate result for planned invocation: {run_id}")
        if run_id not in expected:
            raise ValueError(f"Unplanned result encountered: {run_id}")
        entry = expected[run_id]
        identity = {
            "configuration_id": result.get("configuration_id"),
            "scenario_id": result.get("scenario_id"),
            "replication_index": result.get("replication_index"),
        }
        expected_identity = {key: entry[key] for key in identity}
        if identity != expected_identity:
            raise ValueError(f"Planned identity mismatch for {run_id}")
        if result.get("batch_id") != plan.get("batch_id"):
            raise ValueError(f"Batch identity mismatch for {run_id}")
        if result.get("protocol_version") != plan.get("protocol_version"):
            raise ValueError(f"Protocol identity mismatch for {run_id}")
        if result.get("execution_mode") != plan.get("execution_mode"):
            raise ValueError(f"Execution mode mismatch for {run_id}")
        observed[str(run_id)] = result
    missing = sorted(set(expected) - set(observed))
    return {
        "planned_count": len(expected),
        "observed_count": len(observed),
        "missing_count": len(missing),
        "missing_run_ids": missing,
        "complete": not missing,
    }
