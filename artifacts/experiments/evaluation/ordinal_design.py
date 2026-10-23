"""Pure ordinal design arithmetic; no files, providers, ratings or unblinding.

Includes blinded shared-rating descriptions and synthetic factorial arithmetic,
not a final per-configuration analysis command.
None means a missing rating. It is not a zero and has no implied quality value.
"""
from itertools import product
from typing import Mapping, Sequence

from .agreement import _krippendorff_alpha_ordinal
from .scoring import DIMENSIONS, validate_score_record, _validate_shared_bindings


CELLS = tuple(product((0, 1), repeat=3))


def report_shared_bundle(*, bundle_dir, store_paths, rater_ids):
    """Read verified bundle/stores and enforce their content bindings before reporting."""
    from .score_items import load_bundle_items
    from .scoring import load_score_records
    from .measurement_contract import load_rubric, shared_interaction_payload
    from .blinding import compute_sha256
    from pathlib import Path

    items = load_bundle_items(Path(bundle_dir), None)
    for item in items:
        shared_interaction_payload(item)
    hashes = {item["blinded_item_id"]: item["item_content_sha256"] for item in items}
    rubric_hash = compute_sha256(load_rubric())
    records = [record for path in store_paths for record in load_score_records(Path(path))]
    for record in records:
        if record.get("item_content_sha256") != hashes.get(record["blinded_item_id"]):
            raise ValueError("Score is not bound to the verified bundle item")
        if record.get("rubric_sha256") != rubric_hash:
            raise ValueError("Score is not bound to the current shared rubric snapshot")
    return shared_pair_report(records, item_ids=list(hashes), rater_ids=rater_ids)


def shared_pair_report(records: Sequence[dict], *, item_ids: Sequence[str],
                       rater_ids: Sequence[str]) -> dict:
    """Descriptive blinded two-rater report over an explicit planned inventory.

    Original ratings only: this neither adjudicates nor promotes a judge. Missing
    records and criterion-level unjudgeability remain separate. Caller retains
    assignment provenance and producer/judge failure ledgers outside this report.
    """
    if not item_ids or any(type(x) is not str or not x for x in item_ids) or len(set(item_ids)) != len(item_ids):
        raise ValueError("A non-empty unique planned item inventory is required")
    if len(rater_ids) != 2 or any(type(x) is not str or not x for x in rater_ids) or len(set(rater_ids)) != 2:
        raise ValueError("Exactly two distinct assigned rater IDs are required")
    index = {}
    record_ids = set()
    for record in records:
        if record.get("schema_version") != "0.3.0":
            raise ValueError("Only shared score-record version 0.3.0 is accepted")
        errors = validate_score_record(record)
        if errors:
            raise ValueError("Invalid shared score: " + "; ".join(errors))
        pair = (record["blinded_item_id"], record["rater_id"])
        if pair[0] not in item_ids or pair[1] not in rater_ids:
            raise ValueError("Record lies outside the declared assignment")
        if pair in index or record["score_record_id"] in record_ids:
            raise ValueError("Duplicate rating or record identity")
        index[pair] = record
        record_ids.add(record["score_record_id"])
    _validate_shared_bindings(list(records))
    dimensions = {}
    for dimension in DIMENSIONS:
        sequences, marginals = {}, {}
        for rater in rater_ids:
            values, absent, unjudgeable = [], 0, 0
            for item in item_ids:
                record = index.get((item, rater))
                if record is None:
                    absent += 1
                    values.append(None)
                else:
                    value = record[dimension]
                    unjudgeable += int(value is None)
                    values.append(value)
            sequences[rater] = values
            marginals[rater] = {
                **category_mass(values), "absent_records": absent,
                "unjudgeable": unjudgeable,
                "counts": {score: values.count(score) for score in (0, 1, 2)},
            }
        left, right = (sequences[rater] for rater in rater_ids)
        units = [[a, b] for a, b in zip(left, right) if a is not None and b is not None]
        confusion = [[0] * 3 for _ in range(3)]
        for a, b in units:
            confusion[a][b] += 1
        paired = ordinal_pair_counts(left, right)
        dimensions[dimension] = {
            "raters": marginals, "paired_counts": paired,
            "confusion_rows_first_rater": confusion,
            "exact_agreement": paired["equal"] / paired["paired"] if paired["paired"] else None,
            "extreme_disagreements": confusion[0][2] + confusion[2][0],
            "ordinal_alpha": _krippendorff_alpha_ordinal(units),
            "alpha_population": "COMPLETE_RATING_PAIRS_ONLY",
        }
    return {
        "report_type": "SHARED_ORDINAL_PAIR_DESCRIPTION",
        "planned_items": len(item_ids), "planned_rating_records": len(item_ids) * 2,
        "observed_rating_records": len(records), "rater_order": list(rater_ids),
        "dimensions": dimensions,
        "acceptance_status": "NOT_ASSESSED_BY_DESCRIPTIVE_REPORT",
        "scope": "Blinded assigned items only; not population validity, adjudication or judge acceptance",
    }


def category_mass(scores: Sequence[int | None]) -> dict:
    """Return observed masses over planned slots, retaining the missing category."""
    if not scores:
        return {"planned": 0, "rated": 0, "mass": None, "missing_mass": None}
    if any(x is not None and (type(x) is not int or x not in (0, 1, 2)) for x in scores):
        raise ValueError("Expected scores 0, 1, 2 or missing None")
    counts = {k: scores.count(k) for k in (0, 1, 2)}
    missing = sum(x is None for x in scores)
    return {"planned": len(scores), "rated": len(scores) - missing,
            "mass": {k: n / len(scores) for k, n in counts.items()},
            "missing_mass": missing / len(scores)}


def ordinal_pair_counts(left: Sequence[int | None], right: Sequence[int | None]) -> dict:
    """Compare prespecified matched slots, never zip-truncating or imputing."""
    if len(left) != len(right):
        raise ValueError("Paired slots must have equal lengths")
    category_mass(left)
    category_mass(right)
    result = {"planned": len(left), "paired": 0, "higher": 0, "equal": 0, "lower": 0, "missing": 0}
    for a, b in zip(left, right):
        if a is None or b is None:
            result["missing"] += 1
            continue
        result["paired"] += 1
        result["higher" if a > b else "lower" if a < b else "equal"] += 1
    return result


def factorial_category_coefficients(scores: Mapping[tuple[int, int, int], int | None], category: int) -> dict:
    """Eight-cell effect-coded coefficients of category membership on one complete block.

Returns no coefficient for an incomplete block; the seven beta values are not
seven independent scenarios, p-values, or a composite across quality criteria.
"""
    if any(
        type(cell) is not tuple or len(cell) != 3
        or any(type(bit) is not int or bit not in (0, 1) for bit in cell)
        for cell in scores
    ) or set(scores) != set(CELLS):
        raise ValueError("Exactly the eight declared cells are required")
    if type(category) is not int or category not in (0, 1, 2):
        raise ValueError("Unknown ordinal category")
    category_mass(list(scores.values()))
    if any(value is None for value in scores.values()):
        return {"status": "NOT_ESTIMABLE", "coefficients": None}
    result = {}
    for mask in range(1, 8):
        coefficient = 0.0
        for cell, score in scores.items():
            sign = 1
            for p in range(3):
                if mask & (1 << p):
                    sign *= 2 * cell[p] - 1
            coefficient += sign * int(score == category) / 8
        result[mask] = coefficient
    return {"status": "COMPLETE_BLOCK", "coefficients": result}
