"""Fail-closed reconciliation of local attempt telemetry and OpenRouter exports."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _load_export(csv_path: Path) -> tuple[dict[str, dict[str, str]], list[str]]:
    records: dict[str, dict[str, str]] = {}
    errors: list[str] = []
    if not csv_path.is_file():
        return records, [f"Gateway export missing: {csv_path}"]
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), 2):
            generation_id = (row.get("generation_id") or "").strip()
            if not generation_id:
                errors.append(f"Export row {line_number} has no generation_id")
            elif generation_id in records:
                errors.append(f"Duplicate gateway generation_id: {generation_id}")
            else:
                records[generation_id] = row
    return records, errors


def _discover_local_attempts(repo_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    candidates = set(repo_root.glob("runs/development/**/results/*.json"))
    candidates.update(repo_root.glob("runs/development/**/committed/**/result.json"))
    attempts: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[tuple[str, Any, Any]] = set()
    for path in sorted(candidates):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Unreadable local result {path}: {exc}")
            continue
        run_id = data.get("run_id", path.stem)
        for attempt in data.get("attempts", []):
            key = (run_id, attempt.get("attempt_index"), attempt.get("provider_response_id"))
            if key in seen:
                continue
            seen.add(key)
            attempts.append({"run_id": run_id, **attempt})
    return attempts, errors


def _compare(local: Any, gateway: Any, *, casefold: bool = False) -> str:
    if local is None or gateway is None or str(gateway).strip() == "":
        return "MISSING"
    left, right = str(local), str(gateway)
    if casefold:
        left, right = left.casefold(), right.casefold()
    return "MATCH" if left == right else "MISMATCH"


def reconcile(repo_root: Path) -> dict[str, Any]:
    csv_path = (
        repo_root / "runs" / "development" / "provider_exports" / "openrouter"
        / "openrouter_generations.csv"
    )
    gateway, errors = _load_export(csv_path)
    local, local_errors = _discover_local_attempts(repo_root)
    errors.extend(local_errors)
    rows: list[dict[str, Any]] = []
    for attempt in local:
        generation_id = attempt.get("provider_response_id")
        exported = gateway.get(generation_id) if generation_id else None
        if exported is None:
            rows.append({
                "run_id": attempt["run_id"], "attempt_index": attempt.get("attempt_index"),
                "generation_id": generation_id, "classification": "UNMATCHED", "checks": {},
            })
            continue
        usage = attempt.get("token_usage") or {}
        checks = {
            "returned_model": _compare(
                attempt.get("returned_model"), exported.get("model_permaslug"), casefold=True
            ),
            "returned_provider": _compare(
                attempt.get("returned_provider"), exported.get("provider_name"), casefold=True
            ),
            "prompt_tokens": _compare(usage.get("prompt_tokens"), exported.get("tokens_prompt")),
            "completion_tokens": _compare(
                usage.get("completion_tokens"), exported.get("tokens_completion")
            ),
        }
        states = set(checks.values())
        classification = (
            "VERIFIED_MATCH" if states == {"MATCH"}
            else "MISMATCH" if "MISMATCH" in states
            else "INCOMPLETE"
        )
        rows.append({
            "run_id": attempt["run_id"], "attempt_index": attempt.get("attempt_index"),
            "generation_id": generation_id, "classification": classification,
            "checks": checks, "gateway_cost_total": exported.get("cost_total") or None,
            "local_latency_ms": attempt.get("latency_ms"),
            "gateway_generation_time_ms": exported.get("generation_time_ms") or None,
        })
    return {
        "gateway_record_count": len(gateway), "local_attempt_count": len(local),
        "classification_counts": dict(sorted(Counter(row["classification"] for row in rows).items())),
        "rows": rows, "errors": errors,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# OpenRouter Telemetry Reconciliation", "",
        "> **Status:** DERIVED, FAIL-CLOSED, NON-EXPERIMENTAL TELEMETRY",
        "> A generation-ID match alone is not a verified telemetry match. Model, provider, and token fields are compared independently.",
        "> Cost is reported only from the gateway export; no model price is assumed or hard-coded.",
        "> Credential absence is not inferred by this script. Only a whitelisted subset of fields is rendered.", "",
        f"- Gateway rows: {report['gateway_record_count']}",
        f"- Local attempts: {report['local_attempt_count']}",
        f"- Classifications: `{json.dumps(report['classification_counts'], sort_keys=True)}`",
        f"- Input/read errors: {len(report['errors'])}", "",
        "| Local run | Attempt | Generation ID | Model | Provider | Prompt tokens | Completion tokens | Classification |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        checks = row["checks"]
        lines.append(
            f"| `{row['run_id']}` | {row['attempt_index']} | `{row['generation_id'] or 'NONE'}` | "
            f"{checks.get('returned_model', 'N/A')} | {checks.get('returned_provider', 'N/A')} | "
            f"{checks.get('prompt_tokens', 'N/A')} | {checks.get('completion_tokens', 'N/A')} | "
            f"**{row['classification']}** |"
        )
    if report["errors"]:
        lines.extend(["", "## Errors", ""] + [f"- {error}" for error in report["errors"]])
    return "\n".join(lines) + "\n"


def reconcile_logs(repo_root: Path) -> str:
    return render_markdown(reconcile(repo_root))


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    out_path = repo_root / "pilot" / "WP5_OPENROUTER_RECONCILIATION.md"
    out_path.write_text(reconcile_logs(repo_root), encoding="utf-8", newline="\n")
    print(f"Reconciliation report generated at: {out_path}")


if __name__ == "__main__":
    main()
