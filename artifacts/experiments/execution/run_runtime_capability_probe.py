"""Run bounded, development-only capability probes for a canonical live runtime.

This utility is deliberately work-package neutral. Governance documents decide why a
probe is run; this module only verifies a manifest-bound runtime through the ordinary
runner and writes a sanitized, machine-readable summary.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

from artifacts.experiments.execution.configuration_loader import load_validated_configuration
from artifacts.experiments.execution.live_adapter import LiveAdapter
from artifacts.experiments.execution.runner import (
    RetryPolicy,
    RunContext,
    RunOutcomeCode,
    execute_scenario,
)


DEFAULT_CONFIGURATIONS = (
    "B0_SIMPLE",
    "B1_FULL",
    "S_P1",
    "S_P2",
    "S_P3",
    "G_P1_P2",
    "G_P1_P3",
    "G_P2_P3",
)


class BoundedProbeAdapter(LiveAdapter):
    """Reserve a conservative exposure estimate BEFORE each outbound probe.

    UTF-8 body bytes plus framing allowance are a planning token bound, not a
    provider billing guarantee. Call/body/output limits are enforced locally;
    a provider key spending limit remains the external financial backstop.
    """

    def __init__(self, *, call_cap, cost_cap, input_price, output_price,
                 max_request_bytes, **kwargs):
        super().__init__(**kwargs)
        if type(call_cap) is not int or call_cap < 1:
            raise ValueError("call_cap must be a positive integer")
        if type(max_request_bytes) is not int or max_request_bytes < 1:
            raise ValueError("max_request_bytes must be a positive integer")
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
               for v in (cost_cap, input_price, output_price)):
            raise ValueError("finite positive budget and prices required")
        self.call_cap = call_cap
        self.cost_cap = cost_cap
        self.input_price = input_price
        self.output_price = output_price
        self.max_request_bytes = max_request_bytes
        self.reserved_calls = 0
        self.reserved_cost = 0.0

    def execute(self, request):
        body = request.build_canonical_request_body()
        size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        if size > self.max_request_bytes:
            raise ValueError("probe request exceeds declared byte limit")
        if type(request.max_tokens) is not int or request.max_tokens < 1:
            raise ValueError("probe requires a positive output token limit")
        reserve = ((size + 1024) * self.input_price
                   + request.max_tokens * self.output_price) / 1_000_000
        if self.reserved_calls >= self.call_cap or self.reserved_cost + reserve > self.cost_cap:
            raise ValueError("probe exposure reservation exceeds declared cap")
        self.reserved_calls += 1
        self.reserved_cost += reserve
        return super().execute(request)


def _load_bound_configuration(config_id: str, profile_ref: str, repo_root: Path):
    return load_validated_configuration(
        config_id,
        repo_root=repo_root,
        runtime_profile_ref=profile_ref,
    )


def _attempt_cost(attempt: Any, input_price_per_million: float, output_price_per_million: float) -> float | None:
    if attempt is None or attempt.token_usage is None:
        return None
    return (
        attempt.token_usage.prompt_tokens * input_price_per_million / 1_000_000
        + attempt.token_usage.completion_tokens * output_price_per_million / 1_000_000
    )


def run_probe(
    *,
    repo_root: Path,
    output_root: Path,
    summary_path: Path,
    profile_ref: str,
    scenario_id: str,
    configuration_ids: Iterable[str],
    max_calls: int,
    max_cost_usd: float,
    input_price_per_million: float,
    output_price_per_million: float,
    max_request_bytes: int,
) -> Dict[str, Any]:
    configuration_ids = tuple(configuration_ids)
    if not configuration_ids:
        raise ValueError("at least one configuration is required")
    if len(configuration_ids) > max_calls:
        raise ValueError("configuration count exceeds the predeclared call cap")
    if summary_path.exists():
        raise FileExistsError(f"refusing to overwrite existing summary: {summary_path}")

    adapter = BoundedProbeAdapter(
        default_timeout_seconds=30.0, call_cap=max_calls, cost_cap=max_cost_usd,
        input_price=input_price_per_million, output_price=output_price_per_million,
        max_request_bytes=max_request_bytes,
    )
    rows: List[Dict[str, Any]] = []
    total_cost_usd = 0.0

    for ordinal, config_id in enumerate(configuration_ids, start=1):
        if ordinal > max_calls:
            break
        config = _load_bound_configuration(config_id, profile_ref, repo_root)
        run_id = f"runtime-capability-{config_id}-{scenario_id}-{time.time_ns()}"
        result = execute_scenario(
            configuration_ref=config,
            scenario_ref=scenario_id,
            adapter=adapter,
            run_context=RunContext(
                run_id=run_id,
                protocol_version="0.5.0",
                execution_mode="pilot",
                output_root=output_root,
                retry_policy=RetryPolicy(
                    policy_id="RUNTIME_CAPABILITY_NO_RETRY",
                    max_attempts=1,
                    transient_errors=(),
                ),
            ),
            repo_root=repo_root,
        )
        attempt = result.attempts[0] if len(result.attempts) == 1 else None
        cost_usd = _attempt_cost(
            attempt, input_price_per_million, output_price_per_million
        )
        if cost_usd is not None:
            total_cost_usd += cost_usd
        row = {
            "ordinal": ordinal,
            "run_id": run_id,
            "configuration_id": config_id,
            "scenario_id": scenario_id,
            "outcome": result.final_outcome_code.value,
            "result_persisted": result.result_persisted,
            "preflight_errors": list(result.preflight_errors),
            "cost_usd": cost_usd,
            "attempt": None,
        }
        if attempt is not None:
            row["attempt"] = {
                "adapter_status": attempt.adapter_status,
                "http_status_code": attempt.http_status_code,
                "latency_ms": attempt.latency_ms,
                "requested_model": attempt.requested_model,
                "returned_model": attempt.returned_model,
                "requested_provider_order": list(attempt.requested_provider_order),
                "returned_provider": attempt.returned_provider,
                "provider_response_id": attempt.provider_response_id,
                "token_usage": attempt.token_usage.to_dict() if attempt.token_usage else None,
                "request_body_path": attempt.request_body_path,
                "provider_envelope_path": attempt.provider_envelope_path,
                "model_output_path": attempt.model_output_path,
            }
        rows.append(row)

        cost_display = "UNKNOWN" if cost_usd is None else f"${cost_usd:.6f}"
        print(
            f"{ordinal}/{len(configuration_ids)} {config_id}: "
            f"{result.final_outcome_code.value}, cost={cost_display}"
        )
        if total_cost_usd > max_cost_usd:
            raise RuntimeError(
                f"measured cost ${total_cost_usd:.6f} exceeded cap ${max_cost_usd:.6f}"
            )

        # An unknown charge or runner/transport failure must not permit further spending.
        if attempt is None or attempt.token_usage is None or not result.result_persisted:
            break

        # B0 and B1 form the capability ladder. Do not spend on a traversal when
        # either canonical boundary cannot produce a locally valid record.
        if config_id in {"B0_SIMPLE", "B1_FULL"} and (
            result.final_outcome_code != RunOutcomeCode.COMPLETED_VALID
        ):
            break

    summary = {
        "record_type": "RUNTIME_CAPABILITY_PROBE_SUMMARY",
        "evidence_scope": "DEVELOPMENT_ONLY_ENGINEERING",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile_ref": profile_ref,
        "scenario_id": scenario_id,
        "call_cap": max_calls,
        "actual_calls": sum(1 for row in rows if row["attempt"] is not None),
        "cost_cap_usd": max_cost_usd,
        "max_request_bytes": max_request_bytes,
        "reserved_exposure_usd": adapter.reserved_cost,
        "cost_note": "Token-based estimate at caller-declared prices, not a billing receipt",
        "calculated_cost_usd": (
            total_cost_usd if all(row["cost_usd"] is not None for row in rows) else None
        ),
        "known_calculated_cost_usd": total_cost_usd,
        "unknown_cost_rows": sum(row["cost_usd"] is None for row in rows),
        "input_price_per_million_usd": input_price_per_million,
        "output_price_per_million_usd": output_price_per_million,
        "canonical_boundary_valid": {
            config_id: any(
                row["configuration_id"] == config_id
                and row["outcome"] == RunOutcomeCode.COMPLETED_VALID.value
                for row in rows
            )
            for config_id in ("B0_SIMPLE", "B1_FULL")
        },
        "runs": rows,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-ref", required=True)
    parser.add_argument("--scenario", default="SCN-DEV-POL01-01")
    parser.add_argument("--configurations", nargs="+", default=list(DEFAULT_CONFIGURATIONS))
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--max-cost-usd", type=float, required=True)
    parser.add_argument("--input-price-per-million", type=float, required=True)
    parser.add_argument("--output-price-per-million", type=float, required=True)
    parser.add_argument("--max-request-bytes", type=int, required=True)
    parser.add_argument("--acknowledge-live-call", action="store_true")
    args = parser.parse_args()
    if not args.acknowledge_live_call:
        parser.error("live execution requires --acknowledge-live-call")

    repo_root = Path(__file__).resolve().parent.parent
    summary = run_probe(
        repo_root=repo_root,
        output_root=(repo_root / args.output_root).resolve(),
        summary_path=(repo_root / args.summary_path).resolve(),
        profile_ref=args.profile_ref,
        scenario_id=args.scenario,
        configuration_ids=args.configurations,
        max_calls=args.max_calls,
        max_cost_usd=args.max_cost_usd,
        input_price_per_million=args.input_price_per_million,
        output_price_per_million=args.output_price_per_million,
        max_request_bytes=args.max_request_bytes,
    )
    if not all(summary["canonical_boundary_valid"].values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
