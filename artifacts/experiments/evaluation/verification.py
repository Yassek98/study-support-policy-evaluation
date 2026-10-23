"""D07 partition and natural-sample controls, without material authoring or ratings.

Identity/content/lineage checks cannot establish semantic novelty or reviewer
independence. Those remain documented human-review obligations before sealing.
"""
from collections import Counter
import re

from .blinding import compute_sha256
from .scoring import DIMENSIONS
from ..validation.manifests import CANONICAL_FACTORIAL_MATRIX


PARTITIONS = {"TRAINING", "NATURAL_VERIFICATION", "ANCHOR_CHALLENGE", "FINAL_HELD_OUT"}


def validate_partition_manifest(manifest, *, family_allocation, require_complete=False):
    """Validate coordinator-only provenance, never a rater-facing bundle.

    unit_id is an exact identity; content_sha256 binds the identity-free content;
    lineage_id names its source scenario/construction across rewrites and versions.
    Natural items may share a source scenario across configurations, but no lineage
    may cross partitions. Targets and treatment identities stay coordinator-only.
    """
    if (manifest.get("manifest_sha256") != compute_sha256(
            {k: v for k, v in manifest.items() if k != "manifest_sha256"})
            or manifest.get("record_type") != "MEASUREMENT_PARTITIONS"
            or manifest.get("version") != "0.1.0"):
        raise ValueError("Invalid partition manifest or hash")
    units = manifest.get("units")
    if not isinstance(units, list):
        raise ValueError("Partition units must be a list")
    identities, hashes, lineages, counts = set(), {}, {}, Counter()
    held_scenarios = set()
    natural, targets, held_families = Counter(), Counter(), Counter()
    for unit in units:
        partition = unit.get("partition")
        if partition not in PARTITIONS:
            raise ValueError("Unknown material partition")
        for key in ("unit_id", "lineage_id", "provenance_ref"):
            if not isinstance(unit.get(key), str) or not unit[key].strip():
                raise ValueError("Explicit identity, lineage and provenance are required")
        if unit["unit_id"] in identities:
            raise ValueError("Duplicate partition identity")
        identities.add(unit["unit_id"])
        fingerprint = unit.get("content_sha256")
        if not isinstance(fingerprint, str) or not re.fullmatch("[0-9a-f]{64}", fingerprint):
            raise ValueError("Content fingerprint required")
        if fingerprint in hashes and partition in {"TRAINING", "ANCHOR_CHALLENGE", "FINAL_HELD_OUT"}:
            raise ValueError("Repeated constructed or held-out content")
        for value, owners in ((fingerprint, hashes), (unit["lineage_id"], lineages)):
            if value in owners and owners[value] != partition:
                raise ValueError("Material content or lineage crosses partitions")
            owners[value] = partition
        source = unit.get("source_domain")
        allowed = {"TRAINING": {"DEVELOPMENT", "CONSTRUCTED"},
                   "NATURAL_VERIFICATION": {"DEVELOPMENT"},
                   "ANCHOR_CHALLENGE": {"CONSTRUCTED"}, "FINAL_HELD_OUT": {"HELD_OUT"}}
        if source not in allowed[partition]:
            raise ValueError("Wrong source domain for partition")
        counts[partition] += 1
        if partition in {"NATURAL_VERIFICATION", "FINAL_HELD_OUT"}:
            if unit.get("family") not in family_allocation:
                raise ValueError("Unknown scenario family")
            scenario_id = unit.get("scenario_id")
            if not isinstance(scenario_id, str) or not scenario_id.strip():
                raise ValueError("Scenario identity required")
            if (partition == "NATURAL_VERIFICATION") != scenario_id.startswith("SCN-DEV-"):
                raise ValueError("Development/final scenario identity mismatch")
            # A renamed lineage cannot hide reuse of the exact source scenario.
            previous = lineages.setdefault("scenario:" + scenario_id, partition)
            if previous != partition:
                raise ValueError("Source scenario crosses partitions")
            if partition == "NATURAL_VERIFICATION":
                if unit.get("configuration_id") not in CANONICAL_FACTORIAL_MATRIX:
                    raise ValueError("Unknown verification configuration")
                natural[(unit["family"], unit["configuration_id"])] += 1
            else:
                if scenario_id in held_scenarios:
                    raise ValueError("Duplicate held-out scenario identity")
                held_scenarios.add(scenario_id)
                held_families[unit["family"]] += 1
        if partition in {"TRAINING", "ANCHOR_CHALLENGE"}:
            score = unit.get("target_score")
            if unit.get("target_criterion") not in DIMENSIONS or type(score) is not int or score not in (0, 1, 2):
                raise ValueError("Training/challenge criterion and exact ordinal target required")
            targets[(partition, unit["target_criterion"], score)] += 1
    if require_complete:
        expected = {"TRAINING": 15, "NATURAL_VERIFICATION": len(family_allocation) * 8,
                    "ANCHOR_CHALLENGE": 30, "FINAL_HELD_OUT": sum(family_allocation.values())}
        if dict(counts) != expected:
            raise ValueError("Incomplete selected partition counts")
        if any(natural[(f, c)] != 1 for f in family_allocation for c in CANONICAL_FACTORIAL_MATRIX):
            raise ValueError("Natural verification requires exactly one item per declared stratum")
        if any(targets[(p, d, score)] != n for p, n in (("TRAINING", 1), ("ANCHOR_CHALLENGE", 2))
               for d in DIMENSIONS for score in (0, 1, 2)):
            raise ValueError("Training/challenge target coverage mismatch")
        if dict(held_families) != family_allocation:
            raise ValueError("Held-out family allocation mismatch")
    return {"manifest_sha256": manifest["manifest_sha256"], "counts": dict(counts),
            "scope": "Provenance separation only; no claim of semantic novelty or independent review"}


def select_natural_verification(*, reconciliation, scenario_families, families, selection_seed):
    """Deterministic eligible-item selection; retain failures and uncovered strata.

    Input must be the version-bound development reconciliation, not final runs or
    a hand-picked list of successes. No quality score is read or optimized here.
    """
    if type(selection_seed) is not int:
        raise ValueError("Record an explicit integer selection seed")
    if not reconciliation["coverage"]["complete"]:
        raise ValueError("Reconcile every development invocation before selection")
    results = reconciliation["results"]
    seen, eligible = set(), {}
    for result in results:
        if (result["execution_mode"] != "development" or not result["scenario_id"].startswith("SCN-DEV-")
                or result["configuration_id"] not in CANONICAL_FACTORIAL_MATRIX):
            raise ValueError("Natural verification accepts only declared development outputs")
        if result["run_id"] in seen:
            raise ValueError("Duplicate natural inventory identity")
        seen.add(result["run_id"])
        family = scenario_families.get(result["scenario_id"])
        if family not in families:
            raise ValueError("Missing/unknown development family")
        if result["final_outcome_code"] == "COMPLETED_VALID":
            eligible.setdefault((family, result["configuration_id"]), []).append(result["run_id"])
    selected, gaps = [], []
    for family in families:
        for config in CANONICAL_FACTORIAL_MATRIX:
            candidates = eligible.get((family, config), [])
            if not candidates:
                gaps.append({"family": family, "configuration_id": config})
            else:
                selected.append({"family": family, "configuration_id": config,
                    "run_id": min(candidates, key=lambda run_id: (compute_sha256(
                        {"seed": selection_seed, "run_id": run_id}), run_id))})
    return {"selection_seed": selection_seed, "plan_sha256": reconciliation["plan_sha256"],
            "source_inventory_sha256": compute_sha256(sorted(results, key=lambda row: row["run_id"])),
            "source_outcomes": dict(Counter(r["final_outcome_code"] for r in results)),
            "selected": selected, "missing_strata": gaps, "complete": not gaps}


def verify_natural_sources(*, plan, method, repo_root, output_root, selection,
                          mapping, partitions, items):
    """Reproduce the natural sample from its complete retained development ledger.

    The coordinator mapping never goes to raters. Labels, matching item hashes and
    an accepted agreement statistic cannot replace this producer-to-item check.
    """
    import hashlib
    from pathlib import Path
    from ..execution.reconciliation import reconcile_development, _confined
    from ..execution.planned_execution import prepare_invocation
    from ..validation.scenario_taxonomy import load_scenario_family_taxonomy
    from .blind_runs import SourcePair, verify_source_pair
    from .blinding import build_blinded_item
    from .state_projection import project_state

    root = Path(output_root).resolve()
    ledger = reconcile_development(plan=plan, method=method, repo_root=repo_root, output_root=root)
    taxonomy = load_scenario_family_taxonomy(Path(repo_root))['assignments']
    expected = select_natural_verification(reconciliation=ledger, scenario_families=taxonomy,
        families=list(method['design']['family_allocation']), selection_seed=selection['selection_seed'])
    if selection != expected or not expected['complete']:
        raise ValueError('Natural selection differs from its complete development inventory')
    units = {u['unit_id']: u for u in partitions['units'] if u['partition'] == 'NATURAL_VERIFICATION'}
    presentations = {i['blinded_item_id']: i for i in items}
    if (mapping.get('record_type') != 'NATURAL_SOURCE_MAPPING' or mapping.get('version') != '0.1.0'
            or set(mapping.get('items', {})) != set(units)):
        raise ValueError('Natural mapping must cover exactly the assigned natural items')
    results = {r['run_id']: r for r in ledger['results']}
    selected = {r['run_id'] for r in selection['selected']}
    seen, configs = set(), {}
    for item_id, binding in mapping['items'].items():
        run_id = binding['source_run_id']
        if run_id not in selected or run_id in seen:
            raise ValueError('Natural mapping substitutes or duplicates a selected producer')
        seen.add(run_id)
        result, unit = results[run_id], units[item_id]
        if (any(unit[k] != result[k] for k in ('scenario_id', 'configuration_id'))
                or unit['family'] != taxonomy[result['scenario_id']]
                or unit['lineage_id'] != result['scenario_id']):
            raise ValueError('Natural partition identity differs from its canonical producer')
        record_path = _confined(root, result['run_record_path'])
        result_path = _confined(root, result['result_path'])
        if (binding.get('source_record_sha256') != hashlib.sha256(record_path.read_bytes()).hexdigest()
                or binding.get('source_result_sha256') != hashlib.sha256(result_path.read_bytes()).hexdigest()):
            raise ValueError('Natural source bytes differ from the committed mapping')
        key = (result['configuration_id'], result['replication_index'])
        if key not in configs:
            configs[key], _, _ = prepare_invocation(plan=plan, run_id=run_id, method=method,
                repo_root=repo_root, output_root=root)
        record, _ = verify_source_pair(SourcePair(record_path, result_path), root,
                                       resolved_configuration=configs[key], repo_root=repo_root)
        item = presentations.get(item_id)
        if item is None:
            raise ValueError('Selected natural presentation is missing')
        projected = build_blinded_item(record, project_state(record, 'before'),
            project_state(record, 'after'), item_id, item['blind_batch_id'])
        if projected != item:
            raise ValueError('Natural presentation is not the retained producer projection')
    if seen != selected:
        raise ValueError('Natural mapping omits selected producers')
    return {'plan_sha256': plan['plan_sha256'], 'source_inventory_sha256': expected['source_inventory_sha256'],
            'verified_items': len(seen)}


def _initial_humans(author_lock, external_lock):
    from .measurement_pipeline import validate_rating_lock
    for lock, role in ((author_lock, "AUTHOR_ASSOCIATED"), (external_lock, "EXTERNAL_HUMAN")):
        validate_rating_lock(lock)
        if any(record["rater_role"] != role for record in lock["ratings"].values()):
            raise ValueError("Human initial streams require their declared distinct roles")
    if (author_lock["rater_id"] == external_lock["rater_id"]
            or author_lock["item_hashes"] != external_lock["item_hashes"]
            or author_lock["plan_sha256"] != external_lock["plan_sha256"]
            or author_lock["mapping_sha256"] != external_lock["mapping_sha256"]):
        raise ValueError("Human streams do not have distinct raters and the same assignment")


def verification_item_bindings(item):
    """Separate presentation identity from identity-free substantive content.

    The item self-hash includes its blind ID; it cannot by itself detect renamed
    training/challenge content. The substantive hash uses the common rater payload.
    """
    from .measurement_contract import shared_interaction_payload
    return {"item_content_sha256": item["item_content_sha256"],
            "content_sha256": compute_sha256(shared_interaction_payload(item))}


def verify_partition_items(*, partitions, items, labels):
    """Bind every declared presentation in the requested partitions to actual content."""
    units = {u['unit_id']: u for u in partitions['units'] if u['partition'] in labels}
    presented = {item['blinded_item_id']: item for item in items}
    if len(presented) != len(items) or set(presented) != set(units):
        raise ValueError('Presented inventory differs from its declared partitions')
    for item_id, item in presented.items():
        if any(units[item_id].get(k) != v for k, v in verification_item_bindings(item).items()):
            raise ValueError('Presentation content differs from its partition commitment')
    return len(presented)


def validate_verification_history(*, history, current_partitions, previous):
    """Allow the initial set or one documented new set after a retained failure.

    The caller verifies previous package files and recomputes their assessments.
    This check enforces cycle counts and exact content reuse, not semantic novelty.
    """
    cycle = history.get('correction_cycles')
    if (history.get('record_type') != 'D07_VERIFICATION_HISTORY' or history.get('version') != '0.1.0'
            or type(cycle) is not int or cycle not in (0, 1)
            or history.get('current_partitions_sha256') != current_partitions['manifest_sha256']
            or len(history.get('previous', [])) != cycle or len(previous) != cycle):
        raise ValueError('Verification history requires zero or one fully retained correction')
    for declaration, package in zip(history['previous'], previous):
        if (declaration.get('disposition') != 'DEVELOPMENT_ONLY'
                or not isinstance(declaration.get('reason'), str) or not declaration['reason'].strip()
                or package['assessment'].get('status') != 'NOT_ACCEPTED'
                or package['assessment'].get('partitions_sha256') != package['partitions']['manifest_sha256']):
            raise ValueError('A correction needs its failed assessment, reason and development-only disposition')
        old = {u['content_sha256'] for u in package['partitions']['units']
               if u['partition'] != 'FINAL_HELD_OUT'}
        new = {u['content_sha256'] for u in current_partitions['units']
               if u['partition'] in {'NATURAL_VERIFICATION', 'ANCHOR_CHALLENGE'}}
        if old & new:
            raise ValueError('A corrected verification set reuses previous training or verification content')
    return {'correction_cycles': cycle, 'previous_failed_sets_retained': len(previous)}


def _reference_payload(*, author_lock, external_lock, decisions, review_context):
    """Validate supplied adjudications separately; never edit the initial votes.

    All items, including initially agreed items, need an explicit review decision.
    The context records attestations, not software proof of human independence.
    Run this before dispatching the verification judge; its hash binds that intent.
    """
    _initial_humans(author_lock, external_lock)
    if set(decisions) != set(author_lock["item_hashes"]):
        raise ValueError("Review every assigned item, including initial agreements")
    required_context = {"author_id", "external_id", "initial_ratings_independent",
        "reference_without_judge_access", "external_english_competence",
        "external_development_involvement", "training_completed_ref"}
    if (set(review_context) != required_context
            or review_context["author_id"] != author_lock["rater_id"]
            or review_context["external_id"] != external_lock["rater_id"]
            or review_context["initial_ratings_independent"] is not True
            or review_context["reference_without_judge_access"] is not True
            or any(not isinstance(review_context[key], str) or not review_context[key].strip()
                   for key in required_context - {"initial_ratings_independent", "reference_without_judge_access"})):
        raise ValueError("Explicit independent-rating, training and judge-blind reference attestations required")
    for decision in decisions.values():
        if (set(decision) != {"scores", "status", "reason"}
                or set(decision["scores"]) != set(DIMENSIONS)
                or decision["status"] not in {"RESOLVED", "UNRESOLVED"}
                or not isinstance(decision["reason"], str) or not decision["reason"].strip()
                or any(value is not None and (type(value) is not int or value not in (0, 1, 2))
                       for value in decision["scores"].values())
                or (decision["status"] == "RESOLVED" and None in decision["scores"].values())):
            raise ValueError("Reference requires explicit ordinal decisions and retained resolution reasons")
    reference = {"record_type": "LOCKED_HUMAN_REFERENCE", "version": "0.1.0",
        "scope": "D07_VERIFICATION_REFERENCE",
        "author_lock_sha256": author_lock["lock_sha256"],
        "external_lock_sha256": external_lock["lock_sha256"],
        "item_hashes": author_lock["item_hashes"], "rubric_sha256": author_lock["rubric_sha256"],
        "review_context": review_context, "decisions": decisions}
    reference["reference_sha256"] = compute_sha256(reference)
    return reference


def make_human_reference(*, author_lock, external_lock, decisions, review_context, output_path):
    """Exclusively persist the validated reference after both human streams are locked."""
    from pathlib import Path
    from ..execution.planned_execution import _save_exclusive
    reference = _reference_payload(author_lock=author_lock, external_lock=external_lock,
                                   decisions=decisions, review_context=review_context)
    _save_exclusive(Path(output_path), reference)
    return reference


def _pair_description(left, right):
    """Ordinal alpha for two streams, not the legacy two-rater kappa route."""
    from .agreement import _krippendorff_alpha_ordinal
    from .ordinal_design import ordinal_pair_counts
    counts = ordinal_pair_counts(left, right)
    units = [[a, b] for a, b in zip(left, right) if a is not None and b is not None]
    confusion = [[0] * 3 for _ in range(3)]
    for a, b in units:
        confusion[a][b] += 1
    return {"counts": counts, "ordinal_alpha": _krippendorff_alpha_ordinal(units),
        "exact_agreement": counts["equal"] / counts["paired"] if counts["paired"] else None,
        "extreme_disagreements": confusion[0][2] + confusion[2][0],
        "confusion_rows_first_stream": confusion,
        "marginals": [{"counts": {score: values.count(score) for score in (0, 1, 2)},
                       "missing": values.count(None)} for values in (left, right)]}


def assess_verification(*, method, partitions, author_lock, external_lock, judge_lock,
                        reference, challenge_review):
    """Apply selected D07 rules without inventing votes or optimizing thresholds.

    Requires 80 natural development items and 30 independently reviewed challenges.
    Training and final held-out units never enter agreement calculations. This pure
    assessor does not attest to genuine human independence or promote final access.
    Missing/incomplete evidence returns NOT_ACCEPTED; corrupt bindings raise errors.
    """
    from .measurement_pipeline import validate_rating_lock
    _initial_humans(author_lock, external_lock)
    validate_rating_lock(judge_lock)
    validate_partition_manifest(partitions, family_allocation=method["design"]["family_allocation"],
                                require_complete=True)
    if (judge_lock["rater_id"] in {author_lock["rater_id"], external_lock["rater_id"]}
            or judge_lock["item_hashes"] != author_lock["item_hashes"]
            or judge_lock["plan_sha256"] != author_lock["plan_sha256"]
            or judge_lock["mapping_sha256"] != author_lock["mapping_sha256"]
            or any(record["rater_role"] != "LLM_JUDGE" for record in judge_lock["ratings"].values())):
        raise ValueError("Judge assignment/role mismatch")
    if reference != _reference_payload(author_lock=author_lock, external_lock=external_lock,
            decisions=reference["decisions"], review_context=reference["review_context"]):
        raise ValueError("Reference is not bound to retained initial human ratings")
    # The judge lock must come from reconciled attempts whose intent recorded the
    # already-locked reference, rather than a later consensus adjusted to the judge.
    evidence = judge_lock.get("judge_execution_evidence", {})
    if (evidence.get("prerequisite_reference_sha256") != reference["reference_sha256"]
            or evidence.get("method_sha256") != compute_sha256(method)):
        raise ValueError("Judge execution did not bind this method and pre-existing reference")
    natural = [u for u in partitions["units"] if u["partition"] == "NATURAL_VERIFICATION"]
    challenges = [u for u in partitions["units"] if u["partition"] == "ANCHOR_CHALLENGE"]
    assigned = {u["unit_id"]: u.get("item_content_sha256") for u in natural + challenges}
    if assigned != author_lock["item_hashes"]:
        raise ValueError("Locked item inventory differs from the selected verification partitions")
    targets = {u["unit_id"]: {"criterion": u["target_criterion"], "score": u["target_score"],
                              "content_sha256": u["content_sha256"]} for u in challenges}
    if (set(challenge_review) != {"author_id", "reviewer_id", "targets", "reason"}
            or not all(isinstance(challenge_review[k], str) and challenge_review[k].strip()
                       for k in ("author_id", "reviewer_id", "reason"))
            or challenge_review["author_id"] == challenge_review["reviewer_id"]
            or challenge_review["targets"] != targets):
        raise ValueError("All challenge targets require separate, content-bound independent review")
    issues, dimensions, challenge_failures = [], {}, []
    if any(d["status"] != "RESOLVED" for d in reference["decisions"].values()):
        issues.append("UNRESOLVED_HUMAN_REFERENCE")
    context = reference["review_context"]
    if context["initial_ratings_independent"] is not True or context["reference_without_judge_access"] is not True:
        raise ValueError("Reference lacks required independence attestations")
    for label, lock in (("AUTHOR", author_lock), ("EXTERNAL", external_lock), ("JUDGE", judge_lock)):
        if lock["absent"] or any(record[d] is None for record in lock["ratings"].values() for d in DIMENSIONS):
            issues.append(label + "_INCOMPLETE_RATINGS")
    def values(lock, dimension):
        return [lock["ratings"].get(u["unit_id"], {}).get(dimension) for u in natural]
    for dimension in DIMENSIONS:
        human = _pair_description(values(author_lock, dimension), values(external_lock, dimension))
        judge = _pair_description(values(judge_lock, dimension),
            [reference["decisions"][u["unit_id"]]["scores"][dimension] for u in natural])
        dimensions[dimension] = {"human_human": human, "judge_reference": judge}
        for label, report in (("HUMAN_HUMAN", human), ("JUDGE_REFERENCE", judge)):
            if report["ordinal_alpha"] is None or report["ordinal_alpha"] < 0.80:
                issues.append(f"{dimension}:{label}_ALPHA_BELOW_RULE_OR_NOT_ESTIMABLE")
        if judge["extreme_disagreements"]:
            issues.append(f"{dimension}:EXTREME_JUDGE_REFERENCE_DISAGREEMENT")
    for unit in challenges:
        item_id, dimension, target = unit["unit_id"], unit["target_criterion"], unit["target_score"]
        reference_score = reference["decisions"][item_id]["scores"][dimension]
        judge_score = judge_lock["ratings"].get(item_id, {}).get(dimension)
        if reference_score != target or judge_score != target:
            challenge_failures.append({"unit_id": item_id, "criterion": dimension, "target": target,
                                       "reference": reference_score, "judge": judge_score})
    if challenge_failures:
        issues.append("CHALLENGE_TARGET_MISMATCH")
    report = {"record_type": "D07_VERIFICATION_ASSESSMENT", "version": "0.1.0",
        "status": "NOT_ACCEPTED" if issues else "ACCEPTED", "issues": issues,
        "natural_items": len(natural), "challenge_items": len(challenges),
        "dimensions": dimensions, "challenge_failures": challenge_failures,
        "method_sha256": compute_sha256(method), "partitions_sha256": partitions["manifest_sha256"],
        "reference_sha256": reference["reference_sha256"], "judge_lock_sha256": judge_lock["lock_sha256"],
        "challenge_review_sha256": compute_sha256(challenge_review),
        "scope": "Selected D07 convention on declared material, not validity or proof of human independence"}
    report["assessment_sha256"] = compute_sha256(report)
    return report
