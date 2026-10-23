"""Bind plan rows to selected runtime settings and one durable attempt.

No credential discovery, default transport or automatic retry. Final entry points
require the explicit evidence gate; the current unreleased snapshot stays blocked.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path

from .configuration_loader import freeze_data, load_validated_configuration, unfreeze_data
from .invocation_plan import verify_result_coverage
from .run_record_validator import _get_compiled_validator
from .runner import RetryPolicy, RunContext, execute_scenario


PROFILE = "runtime_profiles/DEV_OPENROUTER_GPT56_LUNA.v0.1.0.json"


def selected_judge_settings(method):
    """Resolve the selected judge without enabling calls or bypassing human verification."""
    from ..evaluation.judge_execution import JudgeSettings
    selected = method["judge_runtime"]
    if (selected["temperature"] != "OMITTED" or selected["top_p"] != "OMITTED"
            or selected["reasoning"] != {"enabled": True, "effort": "low"}
            or selected["endpoint"] != "https://openrouter.ai/api/v1/chat/completions"):
        raise ValueError("Judge settings unsupported by the selected request builder")
    settings = JudgeSettings(model=selected["model"], provider_route=selected["provider_route"],
        provider_response_name=selected["provider_response_name"], max_tokens=selected["max_tokens"],
        seed=selected["seed"], timeout_seconds=selected["timeout_seconds"], temperature=None)
    settings.validate()
    return settings


def prepare_invocation(*, plan, run_id, method, repo_root, output_root, _final_access=None):
    """Validate the plan and selected method before resolving a replica-specific profile."""
    root = Path(repo_root).resolve()
    _get_compiled_validator("invocation_plan.schema.json", str(root / "schemas")).validate(plan)
    verify_result_coverage(plan, [])
    if not method.get("protocol_version") or plan["protocol_version"] != method["protocol_version"]:
        raise ValueError("Invocation plan does not bind the selected protocol version")
    if plan["execution_mode"] != "development":
        if plan["execution_mode"] != "evaluation" or _final_access is None:
            raise ValueError("Without verified final access: development plans only")
        from .final_access import require_access_binding
        require_access_binding(_final_access, plan=plan, method=method, repo_root=root, output_root=output_root)
    entries = [row for row in plan["invocations"] if row["run_id"] == run_id]
    if len(entries) != 1:
        raise ValueError("Run must identify exactly one planned invocation")
    row = entries[0]
    if _final_access is not None:
        from .reconciliation import _confined
        sealed = _final_access.sealed_scenarios[row['scenario_id']]
        path = _confined(root, sealed['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != sealed['sha256']:
            raise ValueError('Final scenario changed after admission')
    if (plan["execution_mode"] == "development") != row["scenario_id"].startswith("SCN-DEV-"):
        raise ValueError("Scenario identity disagrees with development/final execution mode")
    policy = method["attempt_policy"]
    for key, value in {"max_attempts_per_invocation": 1, "automatic_retries": 0,
                       "quality_retries": 0, "fallbacks": False,
                       "healing_or_imputation": False}.items():
        if type(policy[key]) is not type(value) or policy[key] != value:
            raise ValueError("Selected one-attempt policy mismatch")
    selected = method["producer_runtime"]
    seeds = selected["replica_seeds"]
    if (plan["replication_count"] != method["design"]["generation_draws_per_cell"]
            or len(seeds) != plan["replication_count"]
            or any(type(seed) is not int or seed < 0 for seed in seeds)):
        raise ValueError("Replica schedule mismatch")
    config = load_validated_configuration(row["configuration_id"], repo_root=root,
                                          runtime_profile_ref=PROFILE)
    profile = unfreeze_data(config.runtime_profile)
    for key in ("model", "max_tokens", "reasoning", "timeout_seconds"):
        if profile[key] != selected[key]:
            raise ValueError(f"Runtime mismatch: {key}")
    if selected["endpoint"] != "https://openrouter.ai/api/v1/chat/completions":
        raise ValueError("Unsupported selected endpoint")
    if (profile["provider"] != "openrouter"
            or profile["endpoint_family"] != "OPENROUTER_CHAT_COMPLETIONS"
            or profile.get("stop_sequences", []) != []):
        raise ValueError("Provider or stop settings mismatch")
    if profile["routing"] != {
        "provider_order": [selected["provider_response_name"]],
        "allow_fallbacks": False, "require_parameters": True, "data_collection": "deny",
    } or selected["provider_route"].casefold() != selected["provider_response_name"].casefold():
        raise ValueError("Pinned provider route mismatch")
    for key in ("temperature", "top_p"):
        if selected[key] != "OMITTED" or key in profile:
            raise ValueError(f"Sampling omission mismatch: {key}")
    if profile["structured_output"] != {
        "mode": "json_schema", "schema_ref": "schemas/agent_turn_output.schema.json", "strict": True,
    }:
        raise ValueError("Structured-output contract mismatch")
    profile["seed"] = seeds[row["replication_index"] - 1]
    _get_compiled_validator("runtime_profile.schema.json", str(root / "schemas")).validate(profile)
    config = replace(config, runtime_profile=freeze_data(profile))
    context = RunContext(
        run_id=run_id, batch_id=plan["batch_id"], protocol_version=plan["protocol_version"],
        execution_mode=plan["execution_mode"], replication_index=row["replication_index"],
        output_root=Path(output_root),
        retry_policy=RetryPolicy(policy_id="SELECTED_SINGLE_ATTEMPT", max_attempts=1,
                                 backoff_factor=0.0, transient_errors=()),
    )
    return config, context, row


def _save_exclusive(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def _execute_planned(*, plan, run_id, method, repo_root, output_root, adapter, _final_access=None):
    """Claim a row durably before dispatch; an interrupted claim is never redispatched.

An intent without a completion receipt means outcome unknown, not an unused slot.
The existing runner retains raw request/output and its own result ledger. This small
receipt is an orchestration record, not a replacement for that evidence.
"""
    from .method_integrity import verify_method_snapshot
    verify_method_snapshot(method, repository_root=Path(repo_root).resolve().parents[1])
    config, context, row = prepare_invocation(
        plan=plan, run_id=run_id, method=method, repo_root=repo_root, output_root=output_root,
        _final_access=_final_access)
    root = Path(output_root).resolve()
    claim_root = (root / "planned_attempts").resolve()
    claim_root.relative_to(root)
    claim = (claim_root / run_id).resolve()
    claim.relative_to(claim_root)
    claim_root.mkdir(parents=True, exist_ok=True)
    claim.mkdir()  # exclusive: empty/partial claims block reuse too
    method_hash = hashlib.sha256(json.dumps(method, sort_keys=True, allow_nan=False).encode()).hexdigest()
    _save_exclusive(claim / "intent.json", {
        "plan_sha256": plan["plan_sha256"], "method_sha256": method_hash,
        "invocation": row, "resolved_runtime": unfreeze_data(config.runtime_profile),
        "state": "CLAIMED_OUTCOME_UNKNOWN_UNTIL_RECONCILED",
        "final_gate_sha256": _final_access.gate_sha256 if _final_access is not None else None,
    })
    result = execute_scenario(config, row["scenario_id"], adapter, context, Path(repo_root))
    _save_exclusive(claim / "completion.json", {
        "run_id": run_id, "result_path": result.result_path,
        "result_sha256": (hashlib.sha256((root / result.result_path).read_bytes()).hexdigest()
                          if result.result_persisted and result.result_path else None),
        "result_persisted": result.result_persisted,
        "outcome": result.final_outcome_code.value,
        # Keep diagnostic fingerprints and OS error numbers even when no ledger
        # survives. Exception messages can contain response text or credentials.
        "diagnostics": [{
            "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
            "os_errors": re.findall(r"\[(?:WinError|Errno) \d+\]", message),
        } for message in result.preflight_errors],
    })
    return result


def execute_planned_development(**kwargs):
    """Selected development path; cannot promote a plan to final execution."""
    if kwargs["plan"]["execution_mode"] != "development":
        raise ValueError("Development entry point cannot execute final material")
    return _execute_planned(**kwargs)


def execute_planned_final(*, gate_path, **kwargs):
    """Recheck final evidence and earlier attempts before one explicitly supplied adapter call."""
    from .final_access import verify_producer_access
    from .reconciliation import _reconcile_planned
    context = {key: kwargs[key] for key in ("plan", "method", "repo_root", "output_root")}
    access = verify_producer_access(gate_path=gate_path, **context)
    ledger = _reconcile_planned(**context, _final_access=access)
    if any(row["state"] == "OUTCOME_UNKNOWN" for row in ledger["rows"]):
        raise ValueError("An unresolved producer claim blocks further final dispatch")
    return _execute_planned(**kwargs, _final_access=access)


def execute_final_plan(*, gate_path, plan, method, repo_root, output_root, adapter_factory):
    """Execute remaining rows in one admitted plan, with no redispatch of retained rows.

    Admission and existing evidence are checked once at this session boundary.
    Every row still rechecks method bytes and its sealed scenario before claiming.
    An unpersisted outcome stops the session; a retained failure consumes its slot.
    The caller supplies transport through the factory; no credentials are discovered.
    """
    from .final_access import verify_producer_access
    from .reconciliation import _reconcile_planned
    context = dict(plan=plan, method=method, repo_root=repo_root, output_root=output_root)
    access = verify_producer_access(gate_path=gate_path, **context)
    ledger = _reconcile_planned(**context, _final_access=access)
    if any(row['state'] == 'OUTCOME_UNKNOWN' for row in ledger['rows']):
        raise ValueError('An unresolved producer claim blocks further final dispatch')
    pending = {row['run_id'] for row in ledger['rows'] if row['state'] == 'NOT_STARTED'}
    for row in plan['invocations']:
        if row['run_id'] not in pending:
            continue
        adapter = adapter_factory(dict(row))
        result = _execute_planned(**context, run_id=row['run_id'], adapter=adapter, _final_access=access)
        if (not result.result_persisted or result.final_outcome_code.value in
                {'INPUT_CONTRACT_ERROR', 'INTERNAL_RUNNER_ERROR'}):
            break
    return _reconcile_planned(**context, _final_access=access)
