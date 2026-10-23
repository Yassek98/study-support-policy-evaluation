"""Recompute published tables from retained evidence, without providers or new ratings.

Run from the repository or the extracted companion archive:
    python tesi/reproduce_results.py --output tmp/reproduction.json
Requires the repository's Python dependencies. Output is always a new file.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from artifacts.experiments.evaluation.final_analysis import describe_benchmark
from artifacts.experiments.evaluation.level_a import aggregate_level_a, evaluate_execution_result
from artifacts.experiments.execution.method_integrity import method_file_sha256


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(value):
    return json.loads(json.dumps(value))


def reproduce():
    exp = ROOT / "artifacts/experiments"
    report_dir = ROOT / "artifacts/analysis/held_out_exploratory"
    retained = exp / "runs/evaluation/held_out_exploratory_shared"
    report = read(report_dir / "RESULTS.json")
    for name, filename in (("dataset", "analysis_dataset.json"),
                           ("producer_ledger", "producer_ledger.json"),
                           ("rating_lock", "judge_rating_lock.json")):
        if digest(retained / filename) != report["bindings"][name]:
            raise ValueError(f"Changed retained evidence: {filename}")
    method = read(exp / "METHOD_FREEZE_V1.json")
    if digest(exp / "METHOD_FREEZE_V1.json") != report["bindings"]["method"]:
        raise ValueError("Changed method snapshot")
    for artifact in method["frozen_artifacts"]:
        if method_file_sha256(ROOT / artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"Changed frozen dependency: {artifact['path']}")
    plan = read(exp / "FINAL_INVOCATION_PLAN.json")
    if digest(exp / "FINAL_INVOCATION_PLAN.json") != report["bindings"]["plan"]:
        raise ValueError("Changed invocation plan")
    dataset = read(retained / "analysis_dataset.json")
    ledger = read(retained / "producer_ledger.json")
    scenarios = {p.stem: read(p) for p in (exp / "scenarios/held_out").glob("*.json")}
    families = {key: value["family"] for key, value in scenarios.items()}
    quality = describe_benchmark(plan=plan, rows=dataset["rows"],
        scenario_families=families, declared_families=list(method["design"]["family_allocation"]))
    level_a = aggregate_level_a(invocation_plan=plan, results=ledger["results"], repo_root=exp)
    if normalized(quality) != report["quality"]:
        raise ValueError("Recomputed quality differs from the retained report")
    if normalized(level_a) != report["level_a"]:
        raise ValueError("Recomputed Level A differs from the retained report")

    # Independent accounting of complete blocks and the reasons for their absence.
    blocks = defaultdict(list)
    for row in dataset["rows"]:
        blocks[(row["scenario_id"], row["replication_index"])].append(row)
    coverage = {}
    for family in method["design"]["family_allocation"]:
        subset = [rows for (sid, _), rows in blocks.items() if families[sid] == family]
        missing = [rows for rows in subset if any(r["outcome"] != "COMPLETED_VALID" for r in rows)]
        counts = Counter(r["outcome"] for rows in missing for r in rows if r["outcome"] != "COMPLETED_VALID")
        coverage[family] = {"planned_blocks": len(subset), "complete_blocks": len(subset)-len(missing),
                           "missing_blocks": len(missing), "failed_invocations": dict(counts)}
    flags = defaultdict(Counter)
    flag_cases = []
    for result in ledger["results"]:
        assessment = evaluate_execution_result(result, repo_root=exp)
        if assessment.critical_violation:
            safety = result["run_record"]["agent_output"]["policy_outputs"]["safety_envelope"]
            declared = safety["critical_violation"] is True
            external = assessment.target_policy in {"POL-04_AUTONOMY_GATE", "POL-05_HUMAN_BOUNDARY"} and assessment.observable_status == "FAIL"
            kind = "declared_and_external" if declared and external else "declared" if declared else "external"
            flags[assessment.configuration_id][kind] += 1
            flag_cases.append({"run_id": assessment.run_id, "kind": kind,
                               "target_policy": assessment.target_policy,
                               "diagnostics": assessment.diagnostics})
    return {"status": "MATCHES_RETAINED_RESULTS", "source_results_sha256": digest(report_dir / "RESULTS.json"),
            "frozen_dependencies_verified": len(method["frozen_artifacts"]),
            "level_a": level_a, "quality": quality,
            "complete_block_coverage": coverage, "formal_flag_types": dict(flags), "formal_flag_cases": flag_cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = reproduce()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "frozen_dependencies_verified": result["frozen_dependencies_verified"],
                      "complete_block_coverage": result["complete_block_coverage"],
                      "formal_flag_types": result["formal_flag_types"]}, indent=2))
