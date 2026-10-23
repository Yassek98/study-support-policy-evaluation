"""Read-only reconciliation of a plan, local claims and retained evidence.

No repair, redispatch, credential access or automatic promotion of unknown outcomes.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path

from .invocation_plan import verify_result_coverage
from .planned_execution import prepare_invocation
from .configuration_loader import unfreeze_data
from .run_record_validator import _get_compiled_validator
from .runner import reject_duplicate_json_keys, reject_nonstandard_json_constant


def _read(path):
    value = json.loads(path.read_text(encoding="utf-8"),
                      object_pairs_hook=reject_duplicate_json_keys,
                      parse_constant=reject_nonstandard_json_constant)
    if not isinstance(value, dict):
        raise ValueError("Evidence must be a JSON object")
    return value


def _confined(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("Relative evidence path required")
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Evidence escapes execution root") from exc
    return target


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reconcile_planned(*, plan, method, repo_root, output_root, _final_access=None):
    """Return every planned row, verifying identities, receipts and raw artifact hashes.

An intact result can be retained without a completion receipt after interruption.
A claim without a result stays unknown even if its intent file was never completed.
An unclaimed row is not started only when no matching raw/committed evidence exists.
"""
    from .method_integrity import verify_method_snapshot
    verify_method_snapshot(method, repository_root=Path(repo_root).resolve().parents[1])
    root = Path(output_root).resolve()
    expected = {row["run_id"] for row in plan["invocations"]}
    verify_result_coverage(plan, [])
    method_hash = hashlib.sha256(json.dumps(method, sort_keys=True, allow_nan=False).encode()).hexdigest()
    allowed_method_hashes = {method_hash}
    # Explicitly retained pre-amendment development evidence, never a final-run bypass.
    for prior in method.get("preserved_development_batches", []):
        if (plan["execution_mode"] == "development"
                and prior["plan_sha256"] == plan["plan_sha256"]
                and prior["batch_id"] == plan["batch_id"]):
            allowed_method_hashes.add(prior["method_sha256"])
    for directory in ("planned_attempts", "results", "committed"):
        location = _confined(root, directory)
        if not location.exists():
            continue
        for path in location.iterdir():
            name = path.stem if directory == "results" else path.name
            if name not in expected:
                raise ValueError(f"Unplanned evidence in {directory}")
    rows, results, runtime_cache = [], [], {}
    # Validate the frame/runtime contract once even for an entirely unstarted plan.
    # Config/runtime is constant within a configuration/replication slot; do not
    # revalidate a 1,184-row JSON schema once for every still-unstarted invocation.
    first = plan["invocations"][0]
    initial_config, _, _ = prepare_invocation(plan=plan, method=method, repo_root=repo_root,
        output_root=output_root, run_id=first["run_id"], _final_access=_final_access)
    runtime_cache[(first["configuration_id"], first["replication_index"])] = unfreeze_data(initial_config.runtime_profile)
    validator = _get_compiled_validator("run_execution_result.schema.json", str(Path(repo_root) / "schemas"))
    for row in plan["invocations"]:
        run_id = row["run_id"]
        claim = _confined(root, f"planned_attempts/{run_id}")
        intent = _confined(root, f"planned_attempts/{run_id}/intent.json")
        receipt = _confined(root, f"planned_attempts/{run_id}/completion.json")
        candidates = [_confined(root, f"results/{run_id}.json"),
                      _confined(root, f"committed/{run_id}/result.json")]
        found = [path for path in candidates if path.exists()]
        if len(found) > 1:
            raise ValueError("Duplicate result for one invocation")
        raw_dir = _confined(root, "raw")
        raw_exists = raw_dir.exists() and any(raw_dir.glob(f"{run_id}_attempt_*"))
        if not claim.exists():
            if found or raw_exists or _confined(root, f"committed/{run_id}").exists():
                raise ValueError("Unclaimed execution evidence; reconcile manually without redispatch")
            rows.append({**row, "state": "NOT_STARTED", "outcome": None})
            continue
        if not claim.is_dir():
            raise ValueError("Attempt claim must be a directory")
        if intent.exists():
            cache_key = (row["configuration_id"], row["replication_index"])
            if cache_key not in runtime_cache:
                config, _, _ = prepare_invocation(plan=plan, method=method, repo_root=repo_root,
                    output_root=output_root, run_id=run_id, _final_access=_final_access)
                runtime_cache[cache_key] = unfreeze_data(config.runtime_profile)
            data = _read(intent)
            if (data.get("plan_sha256") != plan["plan_sha256"]
                    or data.get("method_sha256") not in allowed_method_hashes
                    or data.get("invocation") != row
                    or data.get("final_gate_sha256") != (_final_access.gate_sha256 if _final_access else None)
                    or data.get("resolved_runtime") != runtime_cache[cache_key]):
                raise ValueError("Intent identity or resolved runtime mismatch")
        elif found or receipt.exists():
            raise ValueError("Result without a readable persisted intent")
        if not found:
            if receipt.exists():
                data = _read(receipt)
                if data.get("run_id") != run_id or data.get("result_persisted") is not False:
                    raise ValueError("Receipt claims missing persisted result")
            rows.append({**row, "state": "OUTCOME_UNKNOWN", "outcome": None})
            continue
        result_path = found[0]
        result = _read(result_path)
        validator.validate(result)
        verify_result_coverage(plan, [result])
        if result.get("result_persisted") is not True or _confined(root, result["result_path"]) != result_path:
            raise ValueError("Result persistence identity mismatch")
        if result["retry_policy"]["max_attempts"] != 1 or len(result["attempts"]) > 1:
            raise ValueError("Unexpected repeated attempt")
        for attempt in result["attempts"]:
            for prefix in ("request_body", "raw_response", "provider_envelope", "model_output"):
                relative, fingerprint = attempt.get(prefix + "_path"), attempt.get(prefix + "_sha256")
                if (relative is None) != (fingerprint is None):
                    raise ValueError("Incomplete evidence binding")
                if relative is not None and _hash(_confined(root, relative)) != fingerprint:
                    raise ValueError("Raw evidence hash mismatch")
        if result["final_outcome_code"] == "COMPLETED_VALID":
            record = _read(_confined(root, result["run_record_path"]))
            if record != result["run_record"] or record.get("run_id") != run_id:
                raise ValueError("Canonical record mismatch")
        if receipt.exists():
            data = _read(receipt)
            if (data.get("run_id") != run_id or data.get("result_path") != result["result_path"]
                    or data.get("result_sha256") != _hash(result_path)
                    or data.get("result_persisted") is not True
                    or data.get("outcome") != result["final_outcome_code"]):
                raise ValueError("Completion receipt mismatch")
        results.append(result)
        rows.append({**row, "state": "RESULT_RETAINED", "outcome": result["final_outcome_code"],
                     "receipt_present": receipt.exists(), "result_sha256": _hash(result_path)})
    return {"plan_sha256": plan["plan_sha256"], "rows": rows,
            "state_counts": dict(Counter(row["state"] for row in rows)),
            "coverage": verify_result_coverage(plan, results), "results": results}


def reconcile_development(**kwargs):
    if kwargs["plan"]["execution_mode"] != "development":
        raise ValueError("Development reconciliation cannot open final material")
    return _reconcile_planned(**kwargs)


def reconcile_final(*, gate_path, **kwargs):
    from .final_access import verify_producer_access
    access = verify_producer_access(gate_path=gate_path, **kwargs)
    return _reconcile_planned(**kwargs, _final_access=access)
