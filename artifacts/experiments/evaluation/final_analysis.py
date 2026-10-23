"""Failure-aware finite-benchmark ordinal tables from reconciled, unblinded rows.

Pure computation. Callers must establish score locking and authorized unblinding;
this module neither reads sealed material nor establishes those prerequisites.
"""
from collections import Counter
from statistics import mean

from ..execution.invocation_plan import verify_result_coverage
from ..execution.runner import RunOutcomeCode
from ..validation.manifests import CANONICAL_FACTORIAL_MATRIX
from .ordinal_design import category_mass, ordinal_pair_counts, factorial_category_coefficients
from .scoring import DIMENSIONS


MISSING = {"producer_not_evaluable", "rating_absent", "criterion_unjudgeable", "judge_failure"}


def describe_benchmark(*, plan, rows, scenario_families, declared_families):
    """Retain every planned invocation and require explicit criterion-level missingness.

Within-family planned slot weights are equal (balanced replication per scenario).
Conditional family distributions normalize those weights over rated slots. Complete
block contrasts normalize over that family's complete blocks, never over other families.
"""
    verify_result_coverage(plan, [])
    if set(plan["configuration_ids"]) != set(CANONICAL_FACTORIAL_MATRIX):
        raise ValueError("The complete eight-configuration design is required")
    if (not declared_families or len(set(declared_families)) != len(declared_families)
            or set(scenario_families) != set(plan["scenario_ids"])
            or set(scenario_families.values()) != set(declared_families)):
        raise ValueError("Exact nonempty declared family inventory required")
    expected = {row["run_id"]: row for row in plan["invocations"]}
    index = {}
    outcomes = {item.value for item in RunOutcomeCode}
    for row in rows:
        run_id = row["run_id"]
        if run_id not in expected or run_id in index:
            raise ValueError("Duplicate or unplanned analysis row")
        if any(row.get(key) != value for key, value in expected[run_id].items()):
            raise ValueError("Analysis identity differs from invocation plan")
        if row["outcome"] not in outcomes:
            raise ValueError("Unknown producer outcome")
        if set(row["scores"]) != set(DIMENSIONS) or set(row["missingness"]) != set(DIMENSIONS):
            raise ValueError("Exactly five scores and missingness dispositions required")
        for dimension in DIMENSIONS:
            value, reason = row["scores"][dimension], row["missingness"][dimension]
            category_mass([value])
            if value is None:
                if reason not in MISSING:
                    raise ValueError("Missing score requires an explicit disposition")
            elif reason is not None:
                raise ValueError("Observed score cannot also be missing")
            if row["outcome"] != "COMPLETED_VALID":
                if value is not None or reason != "producer_not_evaluable":
                    raise ValueError("Invalid producer cannot receive a quality score")
            elif reason == "producer_not_evaluable":
                raise ValueError("Valid producer labelled ineligible")
        index[run_id] = row
    if set(index) != set(expected):
        raise ValueError("Analysis requires every planned row, including failures")
    configurations, dimensions = {}, {}
    grouped = {config: [row for row in rows if row["configuration_id"] == config]
               for config in plan["configuration_ids"]}
    for config, subset in grouped.items():
        configurations[config] = {"planned": len(subset),
            "outcomes": dict(Counter(row["outcome"] for row in subset)),
            "eligible": sum(row["outcome"] == "COMPLETED_VALID" for row in subset)}
    for dimension in DIMENSIONS:
        by_config = {}
        for config, subset in grouped.items():
            families = {}
            for family in declared_families:
                family_rows = [row for row in subset if scenario_families[row["scenario_id"]] == family]
                values = [row["scores"][dimension] for row in family_rows]
                mass = category_mass(values)
                counts = {category: values.count(category) for category in (0, 1, 2)}
                families[family] = {**mass, "counts": counts,
                    "conditional_rated_mass": ({c: count / mass["rated"] for c, count in counts.items()}
                                               if mass["rated"] else None),
                    "missingness": dict(Counter(row["missingness"][dimension] for row in family_rows
                                                if row["scores"][dimension] is None))}
            by_config[config] = {"families": families,
                "macro_planned_mass": {c: mean(v["mass"][c] for v in families.values()) for c in (0, 1, 2)},
                "macro_missing_mass": mean(v["missing_mass"] for v in families.values()),
                "macro_conditional_rated_mass": (
                    {c: mean(v["conditional_rated_mass"][c] for v in families.values()) for c in (0, 1, 2)}
                    if all(v["rated"] for v in families.values()) else None)}
        block_index = {(row["scenario_id"], row["replication_index"], row["configuration_id"]): row
                       for row in rows}
        family_pairs, family_contrasts, repeat_counts = {}, {}, {}
        for family in declared_families:
            scenarios = [scenario for scenario in plan["scenario_ids"] if scenario_families[scenario] == family]
            full, baseline, coefficients, complete_slots = [], [], [], []
            for scenario in scenarios:
                for replica in range(1, plan["replication_count"] + 1):
                    scores = {config: block_index[(scenario, replica, config)]["scores"][dimension]
                              for config in plan["configuration_ids"]}
                    full.append(scores["B1_FULL"])
                    baseline.append(scores["B0_SIMPLE"])
                    if all(value is not None for value in scores.values()):
                        cells = {tuple(int(bit) for bit in CANONICAL_FACTORIAL_MATRIX[config]): value
                                 for config, value in scores.items()}
                        coefficients.append({c: factorial_category_coefficients(cells, c)["coefficients"]
                                             for c in (0, 1, 2)})
                        complete_slots.append({"scenario_id": scenario, "replication_index": replica})
            family_pairs[family] = ordinal_pair_counts(full, baseline)
            family_contrasts[family] = {"planned_blocks": len(full), "complete_blocks": len(coefficients),
                "lost_blocks": len(full) - len(coefficients), "complete_slots": complete_slots,
                "coefficients": ({c: {mask: mean(v[c][mask] for v in coefficients) for mask in range(1, 8)}
                                  for c in (0, 1, 2)} if coefficients else None)}
        for config in plan["configuration_ids"]:
            first, second = [], []
            if plan["replication_count"] == 2:
                for scenario in plan["scenario_ids"]:
                    first.append(block_index[(scenario, 1, config)]["scores"][dimension])
                    second.append(block_index[(scenario, 2, config)]["scores"][dimension])
                repeat_counts[config] = ordinal_pair_counts(first, second)
        dimensions[dimension] = {"configurations": by_config, "B1_vs_B0_by_family": family_pairs,
            "B1_vs_B0_macro_planned_mass": {
                outcome: mean(v[outcome] / v["planned"] for v in family_pairs.values())
                for outcome in ("higher", "equal", "lower", "missing")},
            "B1_vs_B0_macro_paired_mass": (
                {outcome: mean(v[outcome] / v["paired"] for v in family_pairs.values())
                 for outcome in ("higher", "equal", "lower")}
                if all(v["paired"] for v in family_pairs.values()) else None),
            "factorial_by_family": family_contrasts,
            "factorial_macro_coefficients": (
                {c: {mask: mean(v["coefficients"][c][mask] for v in family_contrasts.values())
                     for mask in range(1, 8)} for c in (0, 1, 2)}
                if all(v["complete_blocks"] for v in family_contrasts.values()) else None),
            "repeat_slot_comparison": repeat_counts}
    return {"report_type": "FINITE_BENCHMARK_ORDINAL_DESCRIPTION", "plan_sha256": plan["plan_sha256"],
            "configurations": configurations, "dimensions": dimensions,
            "family_weights": {family: 1 / len(declared_families) for family in declared_families},
            "scope": "No population inference, equivalence, student benefit or composite quality pass"}
