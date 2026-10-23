"""Explicit partial rating assignments; absence is not non-assignment.

No rater recruitment, overlap-size selection or calibration acceptance occurs here.
"""
from itertools import combinations
import re

from .agreement import compute_dimension_agreement, group_by_item
from .scoring import DIMENSIONS, validate_score_record


def validate_assignments(assignments, item_ids):
    inventory = set(item_ids)
    if not inventory or len(inventory) != len(item_ids):
        raise ValueError("A nonempty unique item inventory is required")
    if not isinstance(assignments, dict) or not assignments:
        raise ValueError("An explicit rater assignment is required")
    for rater, items in assignments.items():
        if not isinstance(rater, str) or not re.fullmatch(r"rater_[a-z0-9_]+", rater):
            raise ValueError("Use a pseudonym such as rater_alpha")
        if not isinstance(items, list) or not items or any(not isinstance(i, str) for i in items):
            raise ValueError("Each rater needs a nonempty item list")
        if len(items) != len(set(items)) or not set(items) <= inventory:
            raise ValueError("Duplicate or unknown assigned item")
    if set().union(*(set(items) for items in assignments.values())) != inventory:
        raise ValueError("Every bundle item must have an assigned rater")


def summarize_assignments(records, assignments, item_ids):
    """Describe planned coverage and agreement on pairwise assigned overlap only."""
    validate_assignments(assignments, item_ids)
    planned = {(r, i) for r, items in assignments.items() for i in items}
    observed = set()
    record_ids = set()
    for record in records:
        errors = validate_score_record(record)
        if errors:
            raise ValueError("Invalid score record: " + "; ".join(errors))
        pair = (record["rater_id"], record["blinded_item_id"])
        if pair not in planned or pair in observed or record["score_record_id"] in record_ids:
            raise ValueError("Unexpected or duplicate assigned rating")
        observed.add(pair)
        record_ids.add(record["score_record_id"])
    versions = {(record["schema_version"], record["rubric_version"]) for record in records}
    if len(versions) > 1:
        raise ValueError("Cannot compare assignments across score-record collection versions")
    grouped = group_by_item(records)
    overlap = []
    for a, b in combinations(assignments, 2):
        common = sorted(set(assignments[a]) & set(assignments[b]))
        units = {i: grouped.get(i, {}) for i in common}
        overlap.append({
            "raters": [a, b], "assigned_common_items": len(common),
            "jointly_rated_items": sum((a, i) in observed and (b, i) in observed for i in common),
            "dimensions": [compute_dimension_agreement(units, d, [a, b])._asdict()
                           for d in DIMENSIONS],
        })
    return {
        "evidence_scope": "DESCRIPTIVE_ASSIGNMENT_DIAGNOSTIC_NOT_CALIBRATION_ACCEPTANCE",
        "assigned_cells": len(planned), "submitted_cells": len(observed),
        "missing_assigned_cells": len(planned - observed),
        "unassigned_cells": len(item_ids) * len(assignments) - len(planned),
        "per_rater": {r: {"assigned": len(items),
                          "submitted": sum((r, i) in observed for i in items)}
                      for r, items in assignments.items()},
        "pairwise_overlap": overlap,
    }
