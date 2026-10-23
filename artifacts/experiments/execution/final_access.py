"""Read-only final-evidence gate; no authoring, credentials, transport or promotion.

This is an auditable local workflow boundary, not a security system against a
repository owner. Human independence/authorization remain explicit attestations.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path

from .invocation_plan import verify_result_coverage
from .method_integrity import verify_method_snapshot
from .reconciliation import _read, _confined
from ..evaluation.blinding import compute_sha256
from ..evaluation.verification import assess_verification, validate_partition_manifest
from ..validation.manifests import CANONICAL_FACTORIAL_MATRIX


@dataclass(frozen=True)
class ProducerAccess:
    """Producer admission only: never a human reference or permission to score."""
    plan_sha256: str
    method_sha256: str
    gate_sha256: str
    repo_root: Path
    output_root: Path
    scenario_families: dict
    sealed_scenarios: dict


@dataclass(frozen=True)
class FinalAccess:
    """A verified binding for one local plan/store, not blanket authority to dispatch."""
    plan_sha256: str
    method_sha256: str
    gate_sha256: str
    repo_root: Path
    output_root: Path
    scenario_families: dict
    human_reference: dict
    sealed_scenarios: dict


def require_access_binding(access, *, plan, method, repo_root, output_root):
    if (not isinstance(access, (ProducerAccess, FinalAccess)) or access.plan_sha256 != plan["plan_sha256"]
            or access.method_sha256 != compute_sha256(method)
            or access.repo_root != Path(repo_root).resolve()
            or access.output_root != Path(output_root).resolve()
            or set(access.scenario_families) != set(plan["scenario_ids"])):
        raise ValueError("Verified final access does not bind this plan, method and store")


def _reference(root, reference):
    if set(reference) != {"path", "sha256"}:
        raise ValueError("Evidence reference requires a relative path and byte-exact SHA-256")
    path = _confined(root, reference["path"])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != reference["sha256"]:
        raise ValueError("Final-gate evidence changed or is missing")
    return _read(path)


def _nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def scenario_content_sha256(data):
    """Exclude administrative identity/partition/family labels, not substantive content."""
    return compute_sha256({k: v for k, v in data.items()
                           if k not in {"scenario_id", "version", "split", "family"}})


def verify_held_out_seal(*, seal, method, repo_root, evidence_root):
    """Verify files, allocation and retained review records, not semantic novelty.

    Both initial judgments are retained. Any requested change requires an explicit
    resolution and final confirmations bound to the final scenario bytes. Earlier
    snapshots are referenced and verified when an initial judgment used older bytes.
    """
    from .run_record_validator import load_canonical_scenario
    root, evidence_root = Path(repo_root).resolve(), Path(evidence_root).resolve()
    author_only = seal.get("record_type") == "AUTHOR_REVIEWED_HELD_OUT_SEAL"
    if author_only and (method.get("held_out_review", {}).get("mode") != "AUTHOR_ONLY"
            or seal.get("independence_attested") is not False
            or seal.get("independent_reviewer_id") is not None
            or not _nonblank(seal.get("limitation"))):
        raise ValueError("Author-only seal requires the explicit method amendment and limitation")
    if (seal.get("record_type") not in {"REVIEWED_HELD_OUT_SEAL", "AUTHOR_REVIEWED_HELD_OUT_SEAL"} or seal.get("version") != "0.1.0"
            or seal.get("seal_sha256") != compute_sha256({k: v for k, v in seal.items() if k != "seal_sha256"})
            or seal.get("method_sha256") != compute_sha256(method)
            or not _nonblank(seal.get("author_id"))
            or (not author_only and (not _nonblank(seal.get("independent_reviewer_id"))
                or seal["author_id"] == seal["independent_reviewer_id"]
                or seal.get("independence_attested") is not True))):
        raise ValueError("Held-out seal lacks valid method/reviewer bindings")
    families, content, paths, lineages = {}, set(), set(), set()
    development_content = {scenario_content_sha256(_read(path))
                           for path in (root / "scenarios" / "development").rglob("*.json")}
    for entry in seal["scenarios"]:
        scenario_id = entry["scenario_id"]
        if scenario_id in families or scenario_id.startswith("SCN-DEV-"):
            raise ValueError("Duplicate/development identity in final seal")
        path = _confined(root, entry["path"])
        path.relative_to(root / "scenarios" / "held_out")
        if path in paths or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Sealed scenario file mismatch")
        paths.add(path)
        scenario = load_canonical_scenario(entry["path"], repo_root=root, expected_execution_mode="evaluation")
        if scenario.scenario_id != scenario_id or scenario.family_id != entry["family"]:
            raise ValueError("Sealed scenario identity/family mismatch")
        # ID/version/split alone cannot turn the same JSON into a new scenario.
        raw = _read(path)
        fingerprint = scenario_content_sha256(raw)
        if (fingerprint != entry["content_sha256"] or fingerprint in content or fingerprint in development_content
                or not _nonblank(entry["lineage_id"]) or entry["lineage_id"] in lineages):
            raise ValueError("Sealed content/lineage binding is invalid or repeated")
        content.add(fingerprint)
        lineages.add(entry["lineage_id"])
        review = _reference(evidence_root, entry["review"])
        if review.get("scenario_id") != scenario_id:
            raise ValueError("Review refers to another scenario")
        if author_only:
            vote = review.get("author_initial") or {}
            if (vote.get("status") != "COMPLETED" or vote.get("reviewer_id") != seal["author_id"]
                    or vote.get("judgment") != "ACCEPT" or not _nonblank(vote.get("reason"))
                    or vote.get("scenario_snapshot", {}).get("sha256") != entry["sha256"]
                    or vote.get("scenario_snapshot", {}).get("path") != entry["path"]):
                raise ValueError("Author acceptance must bind the exact final scenario bytes")
            families[scenario_id] = entry["family"]
            continue
        for role, reviewer in (("author_initial", seal["author_id"]),
                               ("independent_initial", seal["independent_reviewer_id"])):
            vote = review[role]
            if (vote.get("reviewer_id") != reviewer or vote.get("judgment") not in {"ACCEPT", "REVISE"}
                    or not _nonblank(vote.get("reason"))):
                raise ValueError("Initial scenario judgment missing or invalid")
            snapshot = vote["scenario_snapshot"]
            if snapshot["sha256"] != entry["sha256"]:
                prior = _reference(evidence_root, snapshot)
                if prior.get("scenario_id") != scenario_id:
                    raise ValueError("Preserved initial scenario snapshot identity mismatch")
            elif snapshot["path"] != entry["path"]:
                raise ValueError("Initial judgment's unchanged snapshot path differs from seal")
        resolution = review["resolution"]
        if (resolution.get("final_sha256") != entry["sha256"]
                or resolution.get("status") != "ACCEPTED_FOR_SEALING"
                or resolution.get("author_confirmed") is not True
                or resolution.get("independent_confirmed") is not True
                or not _nonblank(resolution.get("reason"))):
            raise ValueError("Unresolved scenario review cannot be sealed")
        families[scenario_id] = entry["family"]
    if (len(families) != method["design"]["scenario_count"]
            or dict(Counter(families.values())) != method["design"]["family_allocation"]):
        raise ValueError("Sealed family allocation differs from the selected design")
    present = {p.resolve() for p in (root / "scenarios" / "held_out").rglob("*.json")}
    if present != paths:
        raise ValueError("Unsealed or missing final scenario files")
    return families


def verify_final_access(*, gate_path, plan, method, repo_root, output_root):
    """Full scoring admission; producer-only admission cannot replace D07 evidence."""
    return _verify_access(gate_path=gate_path, plan=plan, method=method,
                          repo_root=repo_root, output_root=output_root, producer_only=False)


def verify_producer_access(*, gate_path, plan, method, repo_root, output_root):
    """Admit the amended producer-only route, or the historical full route."""
    gate = _read(Path(gate_path))
    if gate.get("record_type") == "FINAL_EXECUTION_GATE":
        return verify_final_access(gate_path=gate_path, plan=plan, method=method,
                                   repo_root=repo_root, output_root=output_root)
    return _verify_access(gate_path=gate_path, plan=plan, method=method,
                          repo_root=repo_root, output_root=output_root, producer_only=True)


def _verify_access(*, gate_path, plan, method, repo_root, output_root, producer_only):
    """Recheck source evidence before issuing a plan/store-specific local binding.

    Only the selected accepted-judge route is implemented. A human-only fallback
    needs an explicit methodological amendment, not a boolean bypass here.
    """
    root, output = Path(repo_root).resolve(), Path(output_root).resolve()
    if method != _read(root / "METHOD_FREEZE_V1.json"):
        raise ValueError("Final access requires the repository's canonical method snapshot")
    verify_method_snapshot(method, repository_root=root.parents[1])
    if method.get("execution_readiness") != "PREFLIGHT_COMPLETE_EXTERNAL_GATES_REQUIRED":
        raise ValueError("Preflight remediation is not released")
    verify_result_coverage(plan, [])
    if (plan["execution_mode"] != "evaluation" or plan["protocol_version"] != method["protocol_version"]
            or plan["replication_count"] != method["design"]["generation_draws_per_cell"]
            or set(plan["configuration_ids"]) != set(CANONICAL_FACTORIAL_MATRIX)
            or plan["invocation_count"] != method["design"]["planned_producer_invocations"]):
        raise ValueError("Final plan differs from selected design")
    gate_path = Path(gate_path).resolve()
    evidence_root = gate_path.parent
    gate = _read(gate_path)
    expected_type = "FINAL_PRODUCER_GATE" if producer_only else "FINAL_EXECUTION_GATE"
    if (gate.get("record_type") != expected_type or gate.get("version") != "0.1.0"
            or gate.get("gate_sha256") != compute_sha256({k: v for k, v in gate.items() if k != "gate_sha256"})
            or gate.get("method_sha256") != compute_sha256(method)
            or gate.get("plan_sha256") != plan["plan_sha256"]
            or not _nonblank(gate.get("authorization_ref"))
            or not Path(gate["canonical_output_root"]).is_absolute()
            or Path(gate["canonical_output_root"]).resolve() != output):
        raise ValueError("Final authorization is not bound to this plan/method/store")
    seal = _reference(evidence_root, gate["held_out_seal"])
    families = verify_held_out_seal(seal=seal, method=method, repo_root=root, evidence_root=evidence_root)
    if set(families) != set(plan["scenario_ids"]):
        raise ValueError("Invocation plan differs from reviewed sealed inventory")
    if producer_only:
        if method.get("producer_admission") != "SEALED_AUTHOR_REVIEW_BEFORE_D07":
            raise ValueError("Producer-only admission requires the explicit method amendment")
        partitions = _reference(evidence_root, gate["partitions"])
    else:
        sources = _reference(evidence_root, gate["verification_sources"])
        partitions = _reference(evidence_root, sources["partitions"])
    validate_partition_manifest(partitions, family_allocation=method["design"]["family_allocation"], require_complete=True)
    held = {u["scenario_id"]: u for u in partitions["units"] if u["partition"] == "FINAL_HELD_OUT"}
    if set(held) != set(families):
        raise ValueError("Verification partition inventory differs from final seal")
    for entry in seal["scenarios"]:
        if any(held[entry["scenario_id"]][k] != entry[k] for k in ("family", "content_sha256", "lineage_id")):
                raise ValueError("Sealed final content crosses the verification partition commitment")
    sealed = {entry['scenario_id']: {'path': entry['path'], 'sha256': entry['sha256']}
              for entry in seal['scenarios']}
    if producer_only:
        return ProducerAccess(plan["plan_sha256"], compute_sha256(method), gate["gate_sha256"],
                              root, output, families, sealed)
    receipt_gate_hash = gate["gate_sha256"]
    if "producer_gate" in gate:
        producer_gate = _reference(evidence_root, gate["producer_gate"])
        producer_access = verify_producer_access(
            gate_path=_confined(evidence_root, gate["producer_gate"]["path"]),
            plan=plan, method=method, repo_root=root, output_root=output)
        if (producer_gate.get("record_type") != "FINAL_PRODUCER_GATE"
                or producer_access.sealed_scenarios != sealed
                or _reference(evidence_root, gate["held_out_seal"]) !=
                   _reference(_confined(evidence_root, gate["producer_gate"]["path"]).parent,
                              producer_gate["held_out_seal"])):
            raise ValueError("Scoring gate differs from the original producer commitment")
        receipt_gate_hash = producer_access.gate_sha256
    reference, report = _verify_reference_package(sources=sources, partitions=partitions,
        evidence_root=evidence_root, method=method, root=root)
    _verify_history(sources=sources, partitions=partitions, evidence_root=evidence_root)
    if report["status"] != "ACCEPTED":
        raise ValueError("D07 reference verification did not accept the operational judge")
    return FinalAccess(plan["plan_sha256"], compute_sha256(method), receipt_gate_hash, root, output, families,
                       reference, {entry['scenario_id']: {'path': entry['path'], 'sha256': entry['sha256']}
                                   for entry in seal['scenarios']})



def _verify_reference_package(*, sources, partitions, evidence_root, method, root):
    """Recompute retained source/lock/judge evidence for the current or previous cycle."""
    from ..evaluation.measurement_pipeline import validate_rating_lock, _blind_snapshot
    from ..evaluation.judge_batch import reconcile_judge_bundle
    from .planned_execution import selected_judge_settings
    validate_partition_manifest(partitions, family_allocation=method["design"]["family_allocation"], require_complete=True)
    bundle = _confined(evidence_root, sources["bundle_dir"])
    from ..evaluation.score_items import load_bundle_items
    from ..evaluation.verification import verify_partition_items, verify_natural_sources
    items = load_bundle_items(bundle, None)
    verify_partition_items(partitions=partitions, items=items,
                           labels={'NATURAL_VERIFICATION', 'ANCHOR_CHALLENGE'})
    training = load_bundle_items(_confined(evidence_root, sources['training_bundle_dir']), None)
    verify_partition_items(partitions=partitions, items=training, labels={'TRAINING'})
    natural_plan = _reference(evidence_root, sources['natural_plan'])
    verify_natural_sources(plan=natural_plan, method=method, repo_root=root,
        output_root=_confined(evidence_root, sources['natural_runs_dir']),
        selection=_reference(evidence_root, sources['natural_selection']),
        mapping=_reference(evidence_root, sources['natural_mapping']), partitions=partitions, items=items)
    locks = {}
    for role in ("author", "external", "judge"):
        lock = validate_rating_lock(_reference(evidence_root, sources[role + "_lock"]))
        if (lock['plan_sha256'] != natural_plan['plan_sha256']
                or lock['mapping_sha256'] != sources['natural_mapping']['sha256']):
            raise ValueError('Verification lock differs from the retained source plan/mapping')
        snapshot = _blind_snapshot(bundle, _confined(evidence_root, sources[role + "_store"]),
                                   lock["rater_id"], lock["absent"])
        if any(lock.get(k) != value for k, value in snapshot.items()):
            raise ValueError("Verification source changed after its stream lock")
        locks[role] = lock
    judge = reconcile_judge_bundle(bundle_dir=bundle, session_dir=_confined(evidence_root, sources["judge_session"]),
        store_path=_confined(evidence_root, sources["judge_store"]), settings=selected_judge_settings(method),
        rater_id=locks["judge"]["rater_id"])
    if (judge["recoverable_scores"] or locks["judge"].get("judge_execution_evidence") != {
            "intent_sha256": judge["intent_sha256"], "method_sha256": compute_sha256(method),
            "prerequisite_reference_sha256": judge["prerequisite_reference_sha256"],
            "reconciliation_sha256": compute_sha256(judge)}):
        raise ValueError("Judge verification ledger differs from its locked evidence")
    reference = _reference(evidence_root, sources["reference"])
    report = assess_verification(method=method, partitions=partitions, author_lock=locks["author"],
        external_lock=locks["external"], judge_lock=locks["judge"],
        reference=reference,
        challenge_review=_reference(evidence_root, sources["challenge_review"]))
    return reference, report


def _verify_history(*, sources, partitions, evidence_root):
    from ..evaluation.verification import validate_verification_history
    history = _reference(evidence_root, sources["verification_history"])
    if (type(history.get('correction_cycles')) is not int
            or history['correction_cycles'] not in (0, 1)
            or len(history.get('previous', [])) != history['correction_cycles']):
        raise ValueError('Verification history exceeds the selected correction allowance')
    previous = []
    for entry in history.get("previous", []):
        prior_sources = _reference(evidence_root, entry["sources"])
        prior_method = _reference(evidence_root, entry["method"])
        prior_partitions = _reference(evidence_root, prior_sources["partitions"])
        prior_root = _confined(evidence_root, entry["repository_dir"])
        _, report = _verify_reference_package(sources=prior_sources, partitions=prior_partitions,
            evidence_root=evidence_root, method=prior_method, root=prior_root)
        if report != _reference(evidence_root, entry["assessment"]):
            raise ValueError("Previous D07 assessment differs from retained evidence")
        previous.append({"partitions": prior_partitions, "assessment": report})
    validate_verification_history(history=history, current_partitions=partitions, previous=previous)
