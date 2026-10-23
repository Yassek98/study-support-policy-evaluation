"""
aggregation.py — WP6 Blind Score Aggregation

Aggregates score records without unblinding configuration labels.
All output is keyed by blinded_item_id only.

CRITICAL CONSTRAINT: This module must NOT produce per-configuration rankings.
The join with configuration_id belongs to WP10, after rating lock and
authorized unblinding. Any attempt to access configuration labels from
the sealed manifest is a protocol violation.

Produces NOT_ESTIMABLE when denominators are zero.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any

from .agreement import (
    DIMENSIONS, TERMINAL_STATES, group_by_item,
    compute_dimension_agreement, compute_terminal_state_agreement,
    DimensionAgreement, TerminalStateAgreement,
)
from .scoring import apply_safety_veto, validate_score_record


NOT_ESTIMABLE = "NOT_ESTIMABLE"


@dataclass
class DimensionSummary:
    """Blind aggregation summary for one dimension."""
    dimension: str
    n_rated_items: int
    n_missing: int
    score_distribution: dict[str, int]  # str(score) -> count
    exact_agreement: float | str  # float or NOT_ESTIMABLE
    one_level_disagreements: int
    extreme_disagreements: int
    agreement_statistic: float | None
    agreement_statistic_type: str
    rationale_keywords: list[str] = field(default_factory=list)


@dataclass
class AggregationReport:
    """Full blind aggregation report across all raters and items.

    Configuration labels are NOT present here.
    Per-configuration analysis must be done in WP10 after unblinding.
    """
    n_total_items: int
    n_raters: int
    rater_ids: list[str]  # pseudonyms
    dimension_summaries: list[DimensionSummary]
    terminal_state_summary: dict[str, Any]
    overall_missing_count: int
    safety_veto_count: int
    not_estimable_dimensions: list[str]
    safety_review_signal_count: int = 0
    expected_rating_cells: int = 0
    observed_rating_cells: int = 0
    missing_rating_cells: int = 0
    input_integrity: dict[str, str] = field(default_factory=dict)
    report_note: str = (
        "Blind aggregation — no configuration labels. "
        "Per-configuration analysis: WP10 only."
    )


def aggregate_blind(
    score_records: list[dict[str, Any]],
    rater_ids: list[str] | None = None,
    item_ids: list[str] | None = None,
) -> AggregationReport:
    """Aggregate score records without unblinding.

    Args:
        score_records: List of validated score record dicts from the JSONL store.
        rater_ids: Optional ordered list of rater IDs to include. If None,
                   all rater IDs found in the records are used.

    Returns:
        AggregationReport with blind statistics only.

    Declared raters may have no submitted records; their missing cells remain visible.
    """
    if not score_records and item_ids is None:
        return AggregationReport(
            n_total_items=0, n_raters=0, rater_ids=[],
            dimension_summaries=[], terminal_state_summary={},
            overall_missing_count=0, safety_veto_count=0,
            not_estimable_dimensions=list(DIMENSIONS),
        )

    for index, record in enumerate(score_records):
        if record.get("schema_version") == "0.3.0":
            raise ValueError("Shared ordinal records require the versioned analysis path, not legacy aggregation")
        errors = validate_score_record(record)
        if errors:
            raise ValueError(f"Invalid score record at index {index}: {'; '.join(errors)}")
    versions = {(record["schema_version"], record["rubric_version"]) for record in score_records}
    if len(versions) > 1:
        raise ValueError("Cannot aggregate different score-record collection versions")
    grouped = group_by_item(score_records)
    all_rater_ids = sorted({r.get("rater_id", "UNKNOWN") for r in score_records})

    if rater_ids is None:
        rater_ids = all_rater_ids

    if item_ids is not None:
        if not item_ids or len(item_ids) != len(set(item_ids)):
            raise ValueError("item_ids must be a non-empty unique bundle inventory")
        unexpected_items = set(grouped) - set(item_ids)
        if unexpected_items:
            raise ValueError(f"Scores reference items outside the bundle: {sorted(unexpected_items)}")
        unexpected_raters = set(all_rater_ids) - set(rater_ids)
        if unexpected_raters:
            raise ValueError(f"Scores reference undeclared raters: {sorted(unexpected_raters)}")
        grouped = {item_id: grouped.get(item_id, {}) for item_id in item_ids}

    n_items = len(grouped)

    # Safety veto count
    veto_count = sum(1 for r in score_records
                     if r["schema_version"] == "0.1.0" and apply_safety_veto(r))
    signal_count = sum(1 for r in score_records
                       if r["schema_version"] == "0.2.0" and r.get("safety_review_signals"))

    dim_summaries: list[DimensionSummary] = []
    not_estimable: list[str] = []

    for dim in DIMENSIONS:
        da: DimensionAgreement = compute_dimension_agreement(grouped, dim, rater_ids)

        if da.n_evaluable_items < 1:
            not_estimable.append(dim)
            exact = NOT_ESTIMABLE
        else:
            exact = da.exact_agreement

        dist: dict[str, int] = {}
        for rid in rater_ids:
            for score_val, cnt in da.rater_distributions.get(rid, {}).items():
                key = str(score_val)
                dist[key] = dist.get(key, 0) + cnt

        kappa = da.kappa
        kappa_display = None if (kappa is None or (isinstance(kappa, float) and kappa != kappa)) else kappa

        dim_summaries.append(DimensionSummary(
            dimension=dim,
            n_rated_items=da.n_items,
            n_missing=da.n_missing,
            score_distribution=dist,
            exact_agreement=exact,
            one_level_disagreements=da.one_level_disagreements,
            extreme_disagreements=da.extreme_disagreements,
            agreement_statistic=kappa_display,
            agreement_statistic_type=da.kappa_type,
        ))

    expected_cells = n_items * len(rater_ids)
    observed_cells = len(score_records)
    if any("terminal_state_classification" not in record for record in score_records):
        # The v0.2 ordinary interface deliberately does not collect an
        # outcome.  Do not manufacture an all-positive terminal distribution
        # or attempt agreement over a field that was never observed.
        ts_summary = {
            "collection_status": "NOT_COLLECTED_BY_ORDINARY_INTERFACE",
            "n_items": n_items,
            "n_missing": expected_cells,
            "exact_agreement": NOT_ESTIMABLE,
            "severe_violation_count": NOT_ESTIMABLE,
            "nominal_agreement": NOT_ESTIMABLE,
            "confusion_matrix": {},
        }
    else:
        ts_agr = compute_terminal_state_agreement(grouped, rater_ids)
        ts_summary = {
            "collection_status": "COLLECTED_LEGACY_V0_1",
            "n_items": ts_agr.n_items,
            "n_missing": ts_agr.n_missing,
            "exact_agreement": (
                ts_agr.exact_agreement
                if ts_agr.n_evaluable_items > 0
                else NOT_ESTIMABLE
            ),
            "severe_violation_count": ts_agr.severe_violation_count,
            "nominal_agreement": ts_agr.nominal_agreement,
            "confusion_matrix": ts_agr.confusion_matrix,
        }

    overall_missing = sum(d.n_missing for d in dim_summaries)
    return AggregationReport(
        n_total_items=n_items,
        n_raters=len(rater_ids),
        rater_ids=rater_ids,
        dimension_summaries=dim_summaries,
        terminal_state_summary=ts_summary,
        overall_missing_count=overall_missing,
        safety_veto_count=veto_count,
        safety_review_signal_count=signal_count,
        not_estimable_dimensions=not_estimable,
        expected_rating_cells=expected_cells,
        observed_rating_cells=observed_cells,
        missing_rating_cells=expected_cells - observed_cells,
    )


def aggregate_complete_bundle(
    score_records: list[dict[str, Any]],
    *,
    bundle_items: list[dict[str, Any]],
    expected_rater_ids: list[str],
) -> AggregationReport:
    """Aggregate only against a verified complete bundle and declared roster."""
    if not expected_rater_ids or len(expected_rater_ids) != len(set(expected_rater_ids)):
        raise ValueError("expected_rater_ids must be a non-empty unique roster")
    rating_pairs = [
        (record.get("blinded_item_id"), record.get("rater_id"))
        for record in score_records
    ]
    if len(rating_pairs) != len(set(rating_pairs)):
        raise ValueError("Duplicate blinded-item/rater rating cell")
    item_ids = [item["blinded_item_id"] for item in bundle_items]
    report = aggregate_blind(
        score_records,
        rater_ids=expected_rater_ids,
        item_ids=item_ids,
    )

    def digest(value: Any) -> str:
        encoded = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    report.input_integrity = {
        "bundle_items_sha256": digest(bundle_items),
        "score_records_sha256": digest(score_records),
        "rater_roster_sha256": digest(expected_rater_ids),
    }
    return report


def report_to_dict(report: AggregationReport) -> dict[str, Any]:
    """Convert AggregationReport to a serializable dict."""
    d = asdict(report)
    return d
