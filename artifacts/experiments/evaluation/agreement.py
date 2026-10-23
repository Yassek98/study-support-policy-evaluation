"""Agreement statistics over validated, policy-blind score records."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, NamedTuple

from .scoring import DIMENSIONS, TERMINAL_STATES, load_score_records


class DimensionAgreement(NamedTuple):
    dimension: str
    n_items: int
    n_evaluable_items: int
    n_missing: int
    exact_agreement: float | None
    one_level_disagreements: int
    extreme_disagreements: int
    confusion_matrix: list[list[int]]
    kappa: float | None
    kappa_type: str
    rater_distributions: dict[str, Counter]


class TerminalStateAgreement(NamedTuple):
    n_items: int
    n_evaluable_items: int
    n_missing: int
    exact_agreement: float | None
    severe_violation_count: int
    confusion_matrix: dict[str, dict[str, int]]
    nominal_agreement: float | None


def group_by_item(records: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Group records and reject duplicates instead of silently choosing one."""
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        item = record["blinded_item_id"]
        rater = record["rater_id"]
        if rater in grouped[item]:
            raise ValueError(f"Duplicate rater/item pair: {rater}/{item}")
        grouped[item][rater] = record
    return dict(grouped)


def _weighted_cohen_kappa(a_scores: list[int], b_scores: list[int]) -> float | None:
    if len(a_scores) < 2:
        return None
    observed = [[0] * 3 for _ in range(3)]
    for a, b in zip(a_scores, b_scores):
        observed[a][b] += 1
    n = len(a_scores)
    rows = [sum(row) for row in observed]
    cols = [sum(observed[i][j] for i in range(3)) for j in range(3)]
    observed_agreement = sum(
        (1 - abs(i - j) / 2) * observed[i][j]
        for i in range(3) for j in range(3)
    ) / n
    expected_agreement = sum(
        (1 - abs(i - j) / 2) * rows[i] * cols[j]
        for i in range(3) for j in range(3)
    ) / (n * n)
    if math.isclose(expected_agreement, 1.0):
        return None
    return (observed_agreement - expected_agreement) / (1 - expected_agreement)


def _krippendorff_alpha_ordinal(units: list[list[int]]) -> float | None:
    """Krippendorff alpha with the ordinal coincidence-distance metric."""
    usable = [ratings for ratings in units if len(ratings) >= 2]
    if len(usable) < 2:
        return None
    coincidence = [[0.0] * 3 for _ in range(3)]
    for ratings in usable:
        counts = Counter(ratings)
        denominator = len(ratings) - 1
        for c in range(3):
            for k in range(3):
                pairs = counts[c] * (counts[k] - (1 if c == k else 0))
                coincidence[c][k] += pairs / denominator
    marginals = [sum(coincidence[c]) for c in range(3)]
    total = sum(marginals)
    if total <= 1:
        return None

    def delta(c: int, k: int) -> float:
        if c == k:
            return 0.0
        lo, hi = sorted((c, k))
        between = sum(marginals[lo:hi + 1]) - (marginals[lo] + marginals[hi]) / 2
        return between * between

    observed = sum(coincidence[c][k] * delta(c, k) for c in range(3) for k in range(3)) / total
    expected = sum(marginals[c] * marginals[k] * delta(c, k) for c in range(3) for k in range(3))
    expected /= total * (total - 1)
    if math.isclose(expected, 0.0):
        return None
    return 1 - observed / expected


def compute_dimension_agreement(
    grouped: dict[str, dict[str, dict[str, Any]]],
    dimension: str,
    rater_ids: list[str],
) -> DimensionAgreement:
    if dimension not in DIMENSIONS:
        raise ValueError(f"Unknown dimension: {dimension}")
    if not rater_ids:
        return DimensionAgreement(dimension, len(grouped), 0, 0, None, 0, 0,
                                  [[0] * 3 for _ in range(3)], None, "not_estimable", {})
    missing = 0
    evaluable = 0
    exact = 0
    one_level = 0
    extreme = 0
    confusion = [[0] * 3 for _ in range(3)]
    distributions = {rater: Counter() for rater in rater_ids}
    pairs_a: list[int] = []
    pairs_b: list[int] = []
    units: list[list[int]] = []

    for ratings in grouped.values():
        values: list[int] = []
        for rater in rater_ids:
            record = ratings.get(rater)
            if record is None:
                missing += 1
                continue
            value = record[dimension]
            values.append(value)
            distributions[rater][value] += 1
        if len(values) < 2:
            continue
        evaluable += 1
        units.append(values)
        spread = max(values) - min(values)
        exact += int(spread == 0)
        one_level += int(spread == 1)
        extreme += int(spread == 2)
        if len(rater_ids) == 2:
            a_record = ratings.get(rater_ids[0])
            b_record = ratings.get(rater_ids[1])
            if a_record is not None and b_record is not None:
                a, b = a_record[dimension], b_record[dimension]
                confusion[a][b] += 1
                pairs_a.append(a)
                pairs_b.append(b)

    statistic: float | None = None
    statistic_type = "not_estimable"
    if len(rater_ids) == 2:
        statistic = _weighted_cohen_kappa(pairs_a, pairs_b)
        if statistic is not None:
            statistic_type = "weighted_cohen"
    elif len(rater_ids) > 2:
        statistic = _krippendorff_alpha_ordinal(units)
        if statistic is not None:
            statistic_type = "krippendorff_alpha_ordinal"

    return DimensionAgreement(
        dimension=dimension,
        n_items=len(grouped),
        n_evaluable_items=evaluable,
        n_missing=missing,
        exact_agreement=(exact / evaluable) if evaluable else None,
        one_level_disagreements=one_level,
        extreme_disagreements=extreme,
        confusion_matrix=confusion,
        kappa=statistic,
        kappa_type=statistic_type,
        rater_distributions=distributions,
    )


def compute_terminal_state_agreement(
    grouped: dict[str, dict[str, dict[str, Any]]],
    rater_ids: list[str],
) -> TerminalStateAgreement:
    confusion = {state: {other: 0 for other in TERMINAL_STATES} for state in TERMINAL_STATES}
    missing = evaluable = exact = severe = pair_matches = pair_total = 0
    for ratings in grouped.values():
        values: list[str] = []
        for rater in rater_ids:
            record = ratings.get(rater)
            if record is None:
                missing += 1
                continue
            value = record["terminal_state_classification"]
            values.append(value)
            severe += int(value == "SEVERE_VIOLATION")
        if len(values) < 2:
            continue
        evaluable += 1
        exact += int(len(set(values)) == 1)
        if len(rater_ids) == 2 and all(rater in ratings for rater in rater_ids):
            a = ratings[rater_ids[0]]["terminal_state_classification"]
            b = ratings[rater_ids[1]]["terminal_state_classification"]
            confusion[a][b] += 1
            pair_total += 1
            pair_matches += int(a == b)
    return TerminalStateAgreement(
        n_items=len(grouped),
        n_evaluable_items=evaluable,
        n_missing=missing,
        exact_agreement=(exact / evaluable) if evaluable else None,
        severe_violation_count=severe,
        confusion_matrix=confusion,
        nominal_agreement=(pair_matches / pair_total) if pair_total else None,
    )
