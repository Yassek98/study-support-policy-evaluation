"""
CLI minimale development-only per il Mock Runner (WP3).
Esegue scenari con mock adapter locale senza chiamate esterne di rete.
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from .mock_adapter import MockAdapter
    from .runner import (
        RunContext,
        RunOutcomeCode,
        execute_scenario,
    )
except ImportError:
    from artifacts.experiments.execution.mock_adapter import MockAdapter
    from artifacts.experiments.execution.runner import (
        RunContext,
        RunOutcomeCode,
        execute_scenario,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Development Mock Runner CLI (WP3) - Esecuzione deterministica locale"
    )
    parser.add_argument(
        "--configuration",
        required=True,
        help="ID o percorso della configurazione (es. B0_SIMPLE o configurations/B0_SIMPLE.v0.1.0.json)",
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="ID o percorso dello scenario (es. SCN-DEV-POL01-01)",
    )
    parser.add_argument(
        "--mock-response",
        required=True,
        help="Percorso al file JSON contenente la risposta mock programmata",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Identificativo univoco dell'esecuzione (opzionale, default auto-generato)",
    )
    parser.add_argument(
        "--output-root",
        default="tmp/wp3-cli",
        help="Cartella di output per i ledger di esecuzione (default: tmp/wp3-cli)",
    )
    parser.add_argument(
        "--clock-timestamp",
        default=None,
        help="Timestamp ISO-8601 UTC per il clock deterministico (opzionale)",
    )
    parser.add_argument(
        "--clock-from-mock",
        action="store_true",
        help="Use the single canonical timestamp embedded in the mock fixture.",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Radice repository (opzionale)",
    )

    args = parser.parse_args(argv)

    mock_resp_path = Path(args.mock_response)
    if not mock_resp_path.is_file():
        print(f"[ERRORE] File risposta mock non trovato: {mock_resp_path}", file=sys.stderr)
        return 2

    raw_payload = mock_resp_path.read_text(encoding="utf-8")
    if args.clock_timestamp and args.clock_from_mock:
        print("[ERRORE] --clock-timestamp and --clock-from-mock are mutually exclusive", file=sys.stderr)
        return 2
    fixture_timestamp = None
    if args.clock_from_mock:
        try:
            fixture_payload = json.loads(raw_payload)
            timestamps = {
                value.get("timestamp")
                for value in fixture_payload.get("policy_outputs", {}).values()
                if isinstance(value, dict) and isinstance(value.get("timestamp"), str)
            }
        except (json.JSONDecodeError, AttributeError):
            timestamps = set()
        if len(timestamps) != 1:
            print(
                "[ERRORE] --clock-from-mock requires exactly one policy-output timestamp",
                file=sys.stderr,
            )
            return 2
        fixture_timestamp = timestamps.pop()
    adapter = MockAdapter.from_payload(payload=raw_payload)

    run_id = args.run_id or f"mock-run-{Path(args.configuration).stem}-{Path(args.scenario).stem}"
    out_root = Path(args.output_root)
    repo_root = Path(args.repo_root) if args.repo_root else None

    ctx_kwargs = {
        "run_id": run_id,
        "execution_mode": "development",
        "output_root": out_root,
    }
    if args.clock_timestamp or fixture_timestamp:
        fixed_ts = args.clock_timestamp or fixture_timestamp
        ctx_kwargs["clock_source"] = lambda: fixed_ts

    ctx = RunContext(**ctx_kwargs)

    result = execute_scenario(
        configuration_ref=args.configuration,
        scenario_ref=args.scenario,
        adapter=adapter,
        run_context=ctx,
        repo_root=repo_root,
    )

    print(f"Outcome Code:     {result.final_outcome_code.value}")
    print(f"Result Persisted: {result.result_persisted}")
    if result.result_path:
        print(f"Result Path:      {result.result_path}")
    if result.run_record_path:
        print(f"Run Record Path:  {result.run_record_path}")

    if result.final_outcome_code == RunOutcomeCode.COMPLETED_VALID:
        return 0
    else:
        if result.preflight_errors:
            print(f"Preflight Errors: {'; '.join(result.preflight_errors)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
