"""Lock shared ratings and join authorized mappings to a complete producer plan.

This is local evidence control, not encryption or a substitute for human approval.
Development wrappers reject final material; final publication requires the separate
evidence gate and does not release an unreconciled or unreviewed analysis.
"""
import hashlib
import re
from pathlib import Path

from ..execution.planned_execution import _save_exclusive
from ..execution.reconciliation import _read, _confined, reconcile_development, _reconcile_planned
from .blinding import compute_sha256
from .contracts import require_valid
from .measurement_contract import load_rubric, shared_interaction_payload
from .scoring import DIMENSIONS, load_score_records, validate_score_record
from .score_items import load_bundle_items
from .level_a import aggregate_level_a


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_rating_lock(lock):
    """Validate an immutable stream snapshot; this does not establish rater independence."""
    if (lock.get("record_type") != "LOCKED_SHARED_RATINGS" or lock.get("version") != "0.1.0"
            or lock.get("lock_sha256") != compute_sha256(
                {k: v for k, v in lock.items() if k != "lock_sha256"})):
        raise ValueError("Invalid rating lock")
    hashes, records, absent = lock["item_hashes"], lock["ratings"], lock["absent"]
    if (not hashes or set(records) & set(absent) or set(records) | set(absent) != set(hashes)
            or not isinstance(lock["rater_id"], str) or not lock["rater_id"].strip()
            or lock["rubric_sha256"] != compute_sha256(load_rubric())):
        raise ValueError("Incomplete or incompatible locked assignment")
    seen = set()
    for item_id, record in records.items():
        if (validate_score_record(record) or record.get("schema_version") != "0.3.0"
                or record["blinded_item_id"] != item_id
                or record["item_content_sha256"] != hashes[item_id]
                or record["rater_id"] != lock["rater_id"]
                or record["rubric_sha256"] != lock["rubric_sha256"]
                or record["score_record_id"] in seen):
            raise ValueError("Locked rating binding mismatch")
        seen.add(record["score_record_id"])
    for disposition in absent.values():
        if (set(disposition) != {"reason", "evidence"}
                or disposition["reason"] not in {"rating_absent", "judge_failure"}
                or not isinstance(disposition["evidence"], str) or not disposition["evidence"].strip()):
            raise ValueError("Unqualified locked missingness")
    return lock


def _blind_snapshot(bundle_dir, store_path, rater_id, absent):
    if not isinstance(rater_id, str) or not rater_id.strip():
        raise ValueError("Explicit scoring-stream identity required")
    items = load_bundle_items(Path(bundle_dir), None)
    for item in items:
        shared_interaction_payload(item)
    hashes = {item["blinded_item_id"]: item["item_content_sha256"] for item in items}
    records = load_score_records(Path(store_path))
    rubric_hash = compute_sha256(load_rubric())
    by_item = {}
    for record in records:
        if (record["schema_version"] != "0.3.0" or record["rater_id"] != rater_id
                or record["rubric_sha256"] != rubric_hash
                or record["item_content_sha256"] != hashes.get(record["blinded_item_id"])):
            raise ValueError("Rating is outside the selected rater/item/rubric assignment")
        by_item[record["blinded_item_id"]] = record
    if set(absent) != set(hashes) - set(by_item):
        raise ValueError("Every absent rating requires exactly one explicit disposition")
    for value in absent.values():
        if (set(value) != {"reason", "evidence"}
                or value["reason"] not in {"rating_absent", "judge_failure"}
                or not isinstance(value["evidence"], str) or not value["evidence"].strip()):
            raise ValueError("Absent rating requires a qualified reason and evidence")
    return {"item_hashes": hashes, "rubric_sha256": rubric_hash,
            "ratings": by_item, "absent": absent, "rater_id": rater_id}


def lock_ratings(*, bundle_dir, store_path, rater_id, absent, plan_sha256,
                 mapping_sha256, output_path, judge_execution_evidence=None):
    """Snapshot one assigned scoring stream without opening the sealed mapping.

The mapping commitment comes from bundle creation. Missing ratings are retained,
not fabricated; author/external initial streams and adjudication remain separate.
"""
    if not all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h)
               for h in (plan_sha256, mapping_sha256)):
        raise ValueError("Plan and mapping commitments are required")
    payload = {"record_type": "LOCKED_SHARED_RATINGS", "version": "0.1.0",
        "plan_sha256": plan_sha256, "mapping_sha256": mapping_sha256,
        **_blind_snapshot(bundle_dir, store_path, rater_id, absent)}
    if judge_execution_evidence is not None:
        payload["judge_execution_evidence"] = judge_execution_evidence
    payload["lock_sha256"] = compute_sha256(payload)
    validate_rating_lock(payload)
    _save_exclusive(Path(output_path), payload)
    return payload


def lock_judge_ratings(*, bundle_dir, session_dir, store_path, method, rater_id,
                      plan_sha256, mapping_sha256, output_path, repo_root=None):
    """Derive judge missingness from retained attempts, not an invented rating.

    Unstarted/failed/unknown items remain explicit missing slots. The operator
    chooses to close this scoring stream; no retry or alternate rater is selected.
    """
    from .judge_batch import reconcile_judge_bundle
    from ..execution.planned_execution import selected_judge_settings
    from ..execution.method_integrity import verify_method_snapshot
    root = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[1]
    verify_method_snapshot(method, repository_root=root.parents[1])
    report = reconcile_judge_bundle(bundle_dir=bundle_dir, session_dir=session_dir,
        store_path=store_path, settings=selected_judge_settings(method), rater_id=rater_id)
    if report["recoverable_scores"]:
        raise ValueError("Recover already-persisted judge scores before locking the stream")
    return lock_ratings(bundle_dir=bundle_dir, store_path=store_path, rater_id=rater_id,
        absent=report["absent"], plan_sha256=plan_sha256, mapping_sha256=mapping_sha256,
        output_path=output_path, judge_execution_evidence={
            "intent_sha256": report["intent_sha256"], "method_sha256": compute_sha256(method),
            "prerequisite_reference_sha256": report["prerequisite_reference_sha256"],
            "reconciliation_sha256": compute_sha256(report)})


def _unblind_planned(*, lock_path, bundle_dir, store_path, mapping_path,
                    plan, method, repo_root, output_root, authorization, _final_access=None):
    """Validate the lock before opening the mapping; return every planned analysis row.

authorization is the operator's explicit decision reference, not an inferred grant.
No automatic fallback from judge scores to author scores or averaging of raters.
"""
    if not isinstance(authorization, str) or not authorization.strip():
        raise ValueError("Explicit unblinding decision reference required")
    lock = validate_rating_lock(_read(Path(lock_path)))
    if lock["plan_sha256"] != plan["plan_sha256"]:
        raise ValueError("Lock belongs to a different plan")
    current = _blind_snapshot(bundle_dir, store_path, lock["rater_id"], lock["absent"])
    if any(lock.get(key) != value for key, value in current.items()):
        raise ValueError("Ratings or bundle changed after lock")
    reconciled = _reconcile_planned(plan=plan, method=method, repo_root=repo_root,
                                   output_root=output_root, _final_access=_final_access)
    if not reconciled["coverage"]["complete"]:
        raise ValueError("Unresolved or unstarted producer rows block unblinding")
    if _file_hash(mapping_path) != lock["mapping_sha256"]:
        raise ValueError("Mapping differs from pre-lock commitment")
    mapping = _read(Path(mapping_path))
    require_valid(mapping, "blinding_manifest.schema.json", "unblinding mapping")
    if mapping["blind_batch_id"] != plan["batch_id"]:
        raise ValueError("Mapping batch mismatch")
    results = {result["run_id"]: result for result in reconciled["results"]}
    eligible = {run_id for run_id, result in results.items() if result["final_outcome_code"] == "COMPLETED_VALID"}
    bound, seen_items = {}, set()
    root = Path(output_root).resolve()
    for entry in mapping["entries"]:
        run_id, item_id = entry["source_run_id"], entry["blinded_item_id"]
        if run_id not in eligible or run_id in bound or item_id in seen_items:
            raise ValueError("Mapping is not a bijection over eligible producers")
        result = results[run_id]
        if any(entry[key] != result[key] for key in ("scenario_id", "configuration_id", "replication_index")):
            raise ValueError("Mapping identity mismatch")
        if (entry["blinded_item_sha256"] != lock["item_hashes"].get(item_id)
                or entry["source_result_record_path"] != result["result_path"]
                or entry["source_run_record_path"] != result["run_record_path"]):
            raise ValueError("Mapping source binding mismatch")
        for path_key, hash_key in (("source_result_record_path", "source_result_sha256"),
                                   ("source_run_record_path", "source_record_sha256")):
            if _file_hash(_confined(root, entry[path_key])) != entry[hash_key]:
                raise ValueError("Mapping source hash mismatch")
        bound[run_id] = item_id
        seen_items.add(item_id)
    if set(bound) != eligible or seen_items != set(lock["item_hashes"]):
        raise ValueError("Incomplete mapping inventory")
    rows = []
    for identity in plan["invocations"]:
        result = results[identity["run_id"]]
        item_id = bound.get(identity["run_id"])
        rating = lock["ratings"].get(item_id)
        if item_id is None:
            scores, missing = dict.fromkeys(DIMENSIONS), dict.fromkeys(DIMENSIONS, "producer_not_evaluable")
        elif rating is None:
            scores = dict.fromkeys(DIMENSIONS)
            missing = dict.fromkeys(DIMENSIONS, lock["absent"][item_id]["reason"])
        else:
            scores = {d: rating[d] for d in DIMENSIONS}
            missing = {d: "criterion_unjudgeable" if rating[d] is None else None for d in DIMENSIONS}
        rows.append({**identity, "outcome": result["final_outcome_code"],
                     "scores": scores, "missingness": missing})
    return {"rows": rows, "lock_sha256": lock["lock_sha256"],
            "mapping_sha256": lock["mapping_sha256"], "authorization": authorization,
            "level_a": aggregate_level_a(invocation_plan=plan, results=list(results.values()),
                                         repo_root=Path(repo_root)),
            "scope": "FINAL_REVIEWED_BENCHMARK" if _final_access is not None else "DEVELOPMENT_INTEGRATION_ONLY"}


def unblind_development(**kwargs):
    if kwargs["plan"]["execution_mode"] != "development":
        raise ValueError("Development unblinding cannot open final material")
    return _unblind_planned(**kwargs)


def describe_failure_only_development(*, plan, method, repo_root, output_root,
                                      scenario_families, declared_families):
    """A complete all-invalid batch requires no dummy items, scores or unblinding."""
    from .final_analysis import describe_benchmark
    reconciled = reconcile_development(plan=plan, method=method, repo_root=repo_root, output_root=output_root)
    if not reconciled["coverage"]["complete"]:
        raise ValueError("Unresolved producer rows")
    if any(result["final_outcome_code"] == "COMPLETED_VALID" for result in reconciled["results"]):
        raise ValueError("Eligible responses require the rating-lock pathway")
    results = {result["run_id"]: result for result in reconciled["results"]}
    rows = [{**row, "outcome": results[row["run_id"]]["final_outcome_code"],
             "scores": dict.fromkeys(DIMENSIONS),
             "missingness": dict.fromkeys(DIMENSIONS, "producer_not_evaluable")}
            for row in plan["invocations"]]
    return {"quality": describe_benchmark(plan=plan, rows=rows, scenario_families=scenario_families,
                                          declared_families=declared_families),
            "level_a": aggregate_level_a(invocation_plan=plan, results=list(results.values()),
                                         repo_root=Path(repo_root)),
            "measurement_status": "NO_ELIGIBLE_RESPONSES_NO_RATINGS_OR_UNBLINDING"}


def publish_development_analysis(*, analysis_path, safety_reviews, scenario_families,
                                 declared_families, **unblinding):
    """Persist source-bound tables and separate prose coverage after the rating lock.

    This operator function cannot read evaluation plans. Supplied safety reviews
    are not generated or inferred; incomplete review remains explicitly incomplete.
    A final gate, reviewed seal and authorization still need their own integration.
    """
    from .final_analysis import describe_benchmark
    from .safety_review import safety_inventory, summarize_safety_reviews
    dataset = unblind_development(**unblinding)
    source = reconcile_development(**{key: unblinding[key]
        for key in ("plan", "method", "repo_root", "output_root")})
    inventory = safety_inventory(results=source["results"], output_root=unblinding["output_root"])
    safety = summarize_safety_reviews(items=inventory, reviews=safety_reviews)
    report = {"record_type": "SOURCE_BOUND_DEVELOPMENT_ANALYSIS", "version": "0.1.0",
        "plan_sha256": unblinding["plan"]["plan_sha256"],
        "method_sha256": compute_sha256(unblinding["method"]),
        "producer_reconciliation_sha256": compute_sha256(source),
        "dataset": dataset,
        "quality": describe_benchmark(plan=unblinding["plan"], rows=dataset["rows"],
            scenario_families=scenario_families, declared_families=declared_families),
        "prose_safety": safety,
        "scope": "Development integration only; quality, formal checks and prose review stay separate"}
    report["report_sha256"] = compute_sha256(report)
    _save_exclusive(Path(analysis_path), report)
    return report


def publish_final_analysis(*, gate_path, safety_reviews, **unblinding):
    """Publish one source-bound final report in the canonical store after all gates.

    A separate explicit unblinding decision is still required. This function never
    supplies missing ratings or safety reviews and never invokes either model.
    """
    from ..execution.final_access import verify_final_access
    from .final_analysis import describe_benchmark
    from .safety_review import safety_inventory, summarize_safety_reviews
    from .resource_plan import observed_producer_resources, observed_judge_resources
    context = {key: unblinding[key] for key in ("plan", "method", "repo_root", "output_root")}
    access = verify_final_access(gate_path=gate_path, **context)
    target = _confined(Path(unblinding["output_root"]).resolve(), "analysis/final_report.json")
    if target.exists():
        raise FileExistsError("Final report already exists; preserve the published evidence")
    source = _reconcile_planned(**context, _final_access=access)
    if not source["coverage"]["complete"]:
        raise ValueError("Incomplete producer ledger blocks final analysis")
    safety = summarize_safety_reviews(
        items=safety_inventory(results=source["results"], output_root=unblinding["output_root"]),
        reviews=safety_reviews)
    if safety["readable_missing_author_review"]:
        raise ValueError("Required author prose-safety coverage is incomplete")
    judge_resources = None
    if any(r["final_outcome_code"] == "COMPLETED_VALID" for r in source["results"]):
        from .blind_runs import verify_final_bundle
        from .judge_batch import reconcile_judge_bundle
        from ..execution.planned_execution import selected_judge_settings
        receipt = verify_final_bundle(batch_dir=Path(unblinding["bundle_dir"]).parent,
                                       access=access, reconciliation=source)
        lock = validate_rating_lock(_read(Path(unblinding["lock_path"])))
        expected_store = _confined(access.output_root, "final_judge_scores.jsonl")
        if (Path(unblinding["store_path"]).resolve() != expected_store
                or lock["mapping_sha256"] != receipt["mapping_sha256"]):
            raise ValueError("Final scoring stream differs from selected judge/bundle commitment")
        judge = reconcile_judge_bundle(bundle_dir=unblinding["bundle_dir"],
            session_dir=_confined(access.output_root, "final_judge_session"), store_path=expected_store,
            settings=selected_judge_settings(unblinding["method"]), rater_id=lock["rater_id"])
        if (judge["recoverable_scores"] or lock.get("judge_execution_evidence") != {
                "intent_sha256": judge["intent_sha256"], "method_sha256": access.method_sha256,
                "prerequisite_reference_sha256": judge["prerequisite_reference_sha256"],
                "reconciliation_sha256": compute_sha256(judge)}):
            raise ValueError("Final judge stream is not its reconciled locked evidence")
        judge_resources = observed_judge_resources(reconciliation=judge,
            session_dir=_confined(access.output_root, 'final_judge_session'))
        dataset = _unblind_planned(
            **{key: unblinding[key] for key in (
                "lock_path", "bundle_dir", "store_path", "mapping_path", "plan",
                "method", "repo_root", "output_root", "authorization")},
            _final_access=access)
    else:
        if not isinstance(unblinding.get("authorization"), str) or not unblinding["authorization"].strip():
            raise ValueError("Explicit final analysis decision reference required")
        results = {r["run_id"]: r for r in source["results"]}
        dataset = {"rows": [{**row, "outcome": results[row["run_id"]]["final_outcome_code"],
            "scores": dict.fromkeys(DIMENSIONS), "missingness": dict.fromkeys(DIMENSIONS, "producer_not_evaluable")}
            for row in unblinding["plan"]["invocations"]],
            "level_a": aggregate_level_a(invocation_plan=unblinding["plan"], results=source["results"],
                                         repo_root=Path(unblinding["repo_root"])),
            "authorization": unblinding["authorization"],
            "scope": "NO_ELIGIBLE_RESPONSES_NO_RATINGS_OR_UNBLINDING"}
    report = {"record_type": "SOURCE_BOUND_FINAL_ANALYSIS", "version": "0.1.0",
        "plan_sha256": access.plan_sha256, "method_sha256": access.method_sha256,
        "final_gate_sha256": access.gate_sha256,
        "producer_reconciliation_sha256": compute_sha256(source), "dataset": dataset,
        "quality": describe_benchmark(plan=unblinding["plan"], rows=dataset["rows"],
            scenario_families=access.scenario_families,
            declared_families=list(unblinding["method"]["design"]["family_allocation"])),
        "prose_safety": safety,
        "resources": {'producer': observed_producer_resources(reconciliation=source,
                          output_root=access.output_root), 'judge': judge_resources},
        "scope": "Finite reviewed benchmark; separate conformity, quality and prose safety; no student benefit claim"}
    report["report_sha256"] = compute_sha256(report)
    target.parent.mkdir(parents=True, exist_ok=True)
    _save_exclusive(target, report)
    return report
