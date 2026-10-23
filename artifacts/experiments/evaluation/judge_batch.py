"""Fail-closed orchestration for one verified blinded judge bundle.

This module deliberately has no CLI and performs no credential discovery.  A caller
must supply the credential, the reviewed settings and one injected transport.  The
whole bundle is verified before the first dispatch; every item gets at most one
attempt; the batch stops after any disposition that requires a stop.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from .blinding import compute_sha256
from .judge_execution import JudgeSettings, _money, execute_judge
from .score_items import load_bundle_items
from .scoring import append_score_record, is_duplicate, load_score_records


def _save_exclusive(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())


def execute_judge_bundle(
    *,
    bundle_dir: Path,
    session_dir: Path,
    store_path: Path,
    settings: JudgeSettings,
    transport,
    api_key: str,
    rater_id: str,
    available_usd,
    input_usd_per_million,
    output_usd_per_million,
    max_request_bytes: int = 32768,
    prerequisite_reference: dict | None = None,
    _final_access=None,
) -> dict:
    """Verify, execute and retain a bounded bundle in manifest order.

    ``available_usd`` is a local allowance, not a provider-side billing cap.  Only
    provider-reported actual cost is deducted.  Unknown cost stops the batch and is
    never treated as zero.  Parsed score records are appended only after their
    immutable per-attempt artifacts have been persisted.
    """
    settings.validate()
    allowance = _money(available_usd)
    input_rate = _money(input_usd_per_million)
    output_rate = _money(output_usd_per_million)
    session_dir = Path(session_dir)
    store_path = Path(store_path)

    # This call validates the complete inventory, every schema, self-hash, manifest
    # hash and leakage boundary before any network dispatch or output creation.
    items = load_bundle_items(Path(bundle_dir), None)
    if not items:
        raise ValueError("A judge bundle must contain at least one verified item")
    if prerequisite_reference is not None:
        # Verification ratings must match their reference inventory. Final ratings
        # concern new items, but only the verified final wrapper can supply that gate.
        same_bundle_reference = _final_access is None
        if _final_access is not None:
            from ..execution.final_access import FinalAccess
            if (not isinstance(_final_access, FinalAccess)
                    or prerequisite_reference != _final_access.human_reference
                    or session_dir.resolve() != _final_access.output_root / "final_judge_session"
                    or store_path.resolve() != _final_access.output_root / "final_judge_scores.jsonl"):
                raise ValueError("Final reference requires its verified gate and canonical stores")
        if (prerequisite_reference.get("record_type") != "LOCKED_HUMAN_REFERENCE"
                or prerequisite_reference.get("reference_sha256") != compute_sha256(
                    {k: v for k, v in prerequisite_reference.items() if k != "reference_sha256"})
                or (same_bundle_reference and prerequisite_reference.get("item_hashes") != {
                    item["blinded_item_id"]: item["item_content_sha256"] for item in items})):
            raise ValueError("Verification judge requires its pre-existing, content-bound human reference")
    if any(is_duplicate(rater_id, item["blinded_item_id"], store_path) for item in items):
        raise ValueError("Judge store already contains a rater/item pair from this bundle")
    # Fail now if an existing store is malformed or mixes incompatible bindings.
    load_score_records(store_path)
    if session_dir.exists():
        raise FileExistsError(f"Refusing to reuse judge session directory: {session_dir}")
    session_dir.mkdir(parents=False, exist_ok=False)

    intent = {
        "record_type": "SHARED_JUDGE_BUNDLE_INTENT",
        "version": "0.1.0",
        "bundle_dir_name": Path(bundle_dir).name,
        "item_count": len(items),
        "ordered_item_bindings": [
            {"blinded_item_id": item["blinded_item_id"],
             "item_content_sha256": item["item_content_sha256"]}
            for item in items
        ],
        "settings": asdict(settings),
        "rater_id": rater_id,
        "initial_allowance_usd": str(allowance),
        "input_usd_per_million": str(input_rate),
        "output_usd_per_million": str(output_rate),
        "automatic_retries": 0,
        "prerequisite_reference_sha256": (prerequisite_reference["reference_sha256"]
                                          if prerequisite_reference is not None else None),
    }
    intent["intent_sha256"] = compute_sha256(intent)
    if api_key in json.dumps(intent, ensure_ascii=False):
        raise ValueError("Credential appears in bundle intent")
    _save_exclusive(session_dir / "intent.json", intent)

    attempts = []
    spent = Decimal("0")
    for index, item in enumerate(items, 1):
        try:
            result = execute_judge(
                item=item,
                settings=settings,
                transport=transport,
                api_key=api_key,
                attempt_dir=session_dir / f"attempt-{index:04d}",
                rater_id=rater_id,
                available_usd=allowance - spent,
                input_usd_per_million=input_rate,
                output_usd_per_million=output_rate,
                max_request_bytes=max_request_bytes,
            )
        except ValueError:
            # A pre-dispatch contract/allowance failure is safe to classify here:
            # execute_judge creates its attempt directory only after all such checks.
            # Do not persist exception text because caller-controlled values may be in it.
            if (session_dir / f"attempt-{index:04d}").exists():
                raise  # post-claim failure is unknown, not a safe precheck
            attempts.append({
                "blinded_item_id": item["blinded_item_id"],
                "attempt_id": None,
                "status": "PRECHECK_BLOCKED",
                "actual_cost_usd": None,
                "stop_required": True,
            })
            break
        attempts.append({
            "blinded_item_id": item["blinded_item_id"],
            "attempt_id": result["attempt_id"],
            "status": result["status"],
            "actual_cost_usd": result["actual_cost_usd"],
            "stop_required": result["stop_required"],
        })
        if result["status"] == "PARSED":
            score_path = session_dir / f"attempt-{index:04d}" / "score.json"
            score = json.loads(score_path.read_text(encoding="utf-8"))
            append_score_record(score, store_path)
        if result["actual_cost_usd"] is not None:
            spent += _money(result["actual_cost_usd"])
        if result["stop_required"]:
            break

    summary = {
        "record_type": "SHARED_JUDGE_BUNDLE_RESULT",
        "version": "0.1.0",
        "intent_sha256": intent["intent_sha256"],
        "planned_items": len(items),
        "attempted_items": len(attempts),
        "stored_scores": sum(item["status"] == "PARSED" for item in attempts),
        "actual_cost_usd": str(spent),
        "complete": len(attempts) == len(items) and not any(
            item["stop_required"] for item in attempts
        ),
        "stop_required": len(attempts) != len(items) or any(
            item["stop_required"] for item in attempts
        ),
        "attempts": attempts,
    }
    summary["result_sha256"] = compute_sha256(summary)
    _save_exclusive(session_dir / "result.json", summary)
    return summary


def reconcile_judge_bundle(*, bundle_dir, session_dir, store_path, settings, rater_id):
    """Read every assigned item after a stop, without redispatching any observation.

    A parsed score not appended to the store is recoverable from its retained
    artifact. Partial/empty attempt directories remain unknown. Remaining items
    remain not started, not judged, and never become a quality score of zero.
    """
    import base64
    from ..execution.reconciliation import _read, _confined
    from .measurement_contract import load_rubric, parse_shared_judge_wire_response
    from .judge_execution import build_request, _json_strict
    from .scoring import DIMENSIONS, validate_score_record

    items = load_bundle_items(Path(bundle_dir), None)
    root = Path(session_dir).resolve()
    intent = _read(_confined(root, "intent.json"))
    bindings = [{"blinded_item_id": i["blinded_item_id"], "item_content_sha256": i["item_content_sha256"]}
                for i in items]
    if (intent.get("intent_sha256") != compute_sha256({k: v for k, v in intent.items() if k != "intent_sha256"})
            or intent.get("ordered_item_bindings") != bindings or intent.get("settings") != asdict(settings)
            or intent.get("rater_id") != rater_id or intent.get("automatic_retries") != 0):
        raise ValueError("Judge session intent mismatch")
    expected_names = {f"attempt-{index:04d}" for index in range(1, len(items) + 1)}
    if any(path.name not in expected_names | {"intent.json", "result.json"} for path in root.iterdir()):
        raise ValueError("Unplanned judge session evidence")
    stored = {record["blinded_item_id"]: record for record in load_score_records(Path(store_path))}
    if set(stored) - {i["blinded_item_id"] for i in items}:
        raise ValueError("Score store has items outside this judge assignment")
    rows, recoverable, absent = [], [], {}
    for index, item in enumerate(items, 1):
        item_id = item["blinded_item_id"]
        directory = _confined(root, f"attempt-{index:04d}")
        state, score, cost, status = "NOT_STARTED", None, None, None
        result_sha256 = None
        if directory.exists():
            if not directory.is_dir():
                raise ValueError("Judge attempt claim must be a directory")
            state = "OUTCOME_UNKNOWN"
            prepared_path = _confined(root, f"{directory.name}/prepared.json")
            result_path = _confined(root, f"{directory.name}/result.json")
            if result_path.exists() and not prepared_path.exists():
                raise ValueError("Judge result without prepared identity")
            if prepared_path.exists():
                prepared = _read(prepared_path)
                request = _read(_confined(root, f"{directory.name}/request.json"))
                if (request != build_request(item, settings) or prepared.get("request_sha256") != compute_sha256(request)
                        or prepared.get("item_content_sha256") != item["item_content_sha256"]
                        or prepared.get("blinded_item_id") != item_id or prepared.get("rater_id") != rater_id
                        or prepared.get("settings") != asdict(settings)
                        or prepared.get("rubric_sha256") != compute_sha256(load_rubric())):
                    raise ValueError("Judge prepared request binding mismatch")
                if result_path.exists():
                    result = _read(result_path)
                    result_sha256 = compute_sha256(result)
                    mutable = {"status", "actual_cost_usd", "stop_required"}
                    if any(result.get(key) != value for key, value in prepared.items() if key not in mutable):
                        raise ValueError("Judge result identity mismatch")
                    status, cost = result["status"], result["actual_cost_usd"]
                    if status not in {"TIMEOUT", "TRANSPORT_ERROR", "UNEXPECTED_TRANSPORT_ERROR",
                            "INVALID_ENVELOPE", "CREDENTIAL_ECHO", "PROVIDER_ERROR", "IDENTITY_MISMATCH",
                            "INCOMPLETE_RESPONSE", "INVALID_JUDGMENT", "PARSED"}:
                        raise ValueError("Unknown retained judge terminal status")
                    if cost is not None:
                        _money(cost)
                    response_path = _confined(root, f"{directory.name}/response.json")
                    response = _read(response_path) if response_path.exists() else None
                    if (response is not None and result.get("response_sha256") != compute_sha256(response)):
                        raise ValueError("Judge raw response hash mismatch")
                    if response is None and result.get("response_sha256") is not None:
                        raise ValueError("Missing retained judge response")
                    state = "RESULT_RETAINED"
                    if status == "PARSED":
                        if response is None or response["credential_redacted"] or response["http_status"] != 200:
                            raise ValueError("Parsed judge result lacks eligible raw response")
                        envelope = _json_strict(base64.b64decode(response["body_base64"], validate=True).decode())
                        if (envelope.get("model") != settings.model
                                or envelope.get("provider") != settings.provider_response_name
                                or len(envelope.get("choices", [])) != 1):
                            raise ValueError("Judge response identity mismatch")
                        choice = envelope["choices"][0]
                        message = choice["message"]
                        if choice["finish_reason"] != "stop" or message.get("refusal") or message.get("tool_calls"):
                            raise ValueError("Ineligible judge response")
                        parsed = parse_shared_judge_wire_response(message["content"])
                        score = _read(_confined(root, f"{directory.name}/score.json"))
                        if (validate_score_record(score) or score.get("rater_id") != rater_id
                                or score.get("rater_role") != "LLM_JUDGE" or score.get("blinded_item_id") != item_id
                                or score.get("item_content_sha256") != item["item_content_sha256"]
                                or score.get("rubric_sha256") != prepared["rubric_sha256"]
                                or score["score_record_sha256"] != result.get("score_record_sha256")
                                or any(score[d] != parsed[d]["score"]
                                       or score["criterion_evidence"][d] != parsed[d]["evidence"] for d in DIMENSIONS)):
                            raise ValueError("Judge score is not bound to its retained response")
                        if item_id not in stored:
                            recoverable.append(score)
        if item_id in stored and stored[item_id] != score:
            raise ValueError("Stored judge score lacks a matching retained attempt")
        if score is None:
            absent[item_id] = {"reason": "rating_absent" if state == "NOT_STARTED" else "judge_failure",
                               "evidence": f"Judge session {root.name}, slot {index}, {state}, {status or 'no terminal receipt'}"}
        rows.append({"blinded_item_id": item_id, "state": state, "status": status,
                     "actual_cost_usd": cost, "stored": item_id in stored,
                     "result_sha256": result_sha256})
    return {"intent_sha256": intent["intent_sha256"], "rows": rows,
            "prerequisite_reference_sha256": intent.get("prerequisite_reference_sha256"),
            "recoverable_scores": recoverable, "absent": absent,
            "scope": "One retained session; no redispatch or global cross-store deduplication"}


def recover_judge_scores(**kwargs):
    """Append only already-persisted, reverified scores; never repeat model calls."""
    report = reconcile_judge_bundle(**kwargs)
    for score in report["recoverable_scores"]:
        append_score_record(score, Path(kwargs["store_path"]))
    return reconcile_judge_bundle(**kwargs)


def execute_final_judge_bundle(*, gate_path, plan, method, repo_root, output_root,
                               batch_dir, transport, api_key, rater_id,
                               available_usd, input_usd_per_million, output_usd_per_million):
    """Selected final judge route, reusing the one-attempt component after evidence checks."""
    from ..execution.final_access import verify_final_access
    from ..execution.reconciliation import _reconcile_planned, _confined
    from ..execution.planned_execution import selected_judge_settings
    from .blind_runs import verify_final_bundle
    context = dict(plan=plan, method=method, repo_root=repo_root, output_root=output_root)
    access = verify_final_access(gate_path=gate_path, **context)
    ledger = _reconcile_planned(**context, _final_access=access)
    if not ledger["coverage"]["complete"]:
        raise ValueError("Incomplete producer ledger blocks final judging")
    verify_final_bundle(batch_dir=batch_dir, access=access, reconciliation=ledger)
    root = Path(output_root).resolve()
    return execute_judge_bundle(bundle_dir=Path(batch_dir) / "calibration_bundle",
        session_dir=_confined(root, "final_judge_session"), store_path=_confined(root, "final_judge_scores.jsonl"),
        settings=selected_judge_settings(method), transport=transport, api_key=api_key, rater_id=rater_id,
        available_usd=available_usd, input_usd_per_million=input_usd_per_million,
        output_usd_per_million=output_usd_per_million,
        prerequisite_reference=access.human_reference, _final_access=access)
