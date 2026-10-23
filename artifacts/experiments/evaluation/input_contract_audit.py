"""Offline census of actual development inputs; never invokes a model or reads held-out files.

Run from the repository root with ``python -m artifacts.experiments.evaluation.input_contract_audit``.
Output is diagnostic JSON, not ratings, an automatic semantic review, or empirical evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..execution.configuration_loader import load_validated_configuration, unfreeze_data
from ..execution.prompt_composer import compose_prompt
from ..execution.run_record_validator import load_canonical_scenario, project_initial_state
from ..execution.runner import project_model_input
from .level_a import ORACLE_TARGETS, expected_activation
from ..execution.shared_context import project_shared_context
from ..execution.scenario_expectations import resolve_expectations


CONFIGURATIONS = ("B0_SIMPLE", "S_P1", "S_P2", "S_P3", "G_P1_P2", "G_P1_P3", "G_P2_P3", "B1_FULL")
POLICIES = ("POL-01_IF_THEN_PLAN", "POL-02_GRADED_SCAFFOLD", "POL-03_ACCEPTED_NEXT_ACTION")


def inspect_shared_context(raw: dict[str, Any]) -> dict[str, Any]:
    """Compatibility entry point: inspect implemented context and author-only provenance."""
    if raw.get("split") != "development":
        raise ValueError("Context inspection accepts development scenarios only")
    initial = raw["initial_state"]
    context = project_shared_context(initial)
    sources: dict[str, Any] = {}
    if "objective_declared" in initial:
        sources["objective"] = "initial_state.objective_declared"
    if "previous_plan_in_progress" in initial:
        sources["prior_plan"] = "initial_state.previous_plan_in_progress"
    for key in ("scaffold_state", "action_record", "pending_proposed_action",
                "pending_action", "pol03_accepted_action"):
        container = initial.get(key)
        if container is None:
            continue
        if key != "scaffold_state":
            sources["prior_action"] = f"initial_state.{key}"
        for index, fact in enumerate(container.get("grounded_facts", ())):
            sources.setdefault("prior_facts", {}).setdefault(fact, []).append(
                f"initial_state.{key}.grounded_facts[{index}]")
    return {"status": "IMPLEMENTED_SHARED_CONTEXT", "shared_context": context,
            "author_only_provenance": sources}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def audit_development_inputs(root: Path, *, include_provenance: bool = False) -> dict[str, Any]:
    root = root.resolve()
    configurations = {name: load_validated_configuration(
        f"configurations/{name}.v0.1.0.json", repo_root=root) for name in CONFIGURATIONS}
    prompts = {name: compose_prompt(config, repo_root=root) for name, config in configurations.items()}
    rows = []
    for path in sorted((root / "scenarios" / "development").glob("*.json")):
        if path.is_symlink() or path.resolve().parent != root / "scenarios" / "development":
            raise ValueError("Development audit refuses redirected scenario files")
        scenario = load_canonical_scenario(path, repo_root=root, expected_execution_mode="development")
        raw = unfreeze_data(scenario.raw_data)
        views = {}
        for name, config in configurations.items():
            state = project_initial_state(scenario, config)
            model_input = project_model_input(scenario, config, prompts[name], state)
            views[name] = {
                "user_message": model_input.user_message,
                "dialogue_context": unfreeze_data(model_input.dialogue_context),
                "runtime": unfreeze_data(model_input.runtime_parameters),
            }
        contexts = [view["dialogue_context"] for view in views.values()]
        # Compare fact sets, not state IDs/versions. Action text and status are
        # separately retained because proposals must not be converted to facts.
        fact_sets = []
        actions = []
        for context in contexts:
            facts = set(context["shared_context"]["prior_facts"])
            for key in ("scaffold_state", "action_record"):
                facts.update((context.get(key) or {}).get("grounded_facts", []))
            fact_sets.append(sorted(facts))
            actions.append(context["shared_context"]["prior_action"])
        target = raw["evaluated_policy"]
        if target == "CROSS_POLICY_INTERACTION":
            target = ORACLE_TARGETS[raw["oracle_ref"]]
        expected = {policy: expected_activation(raw, policy) for policy in POLICIES}
        rows.append({
            "scenario_id": raw["scenario_id"],
            "target_policy": target,
            "same_user_message": len({view["user_message"] for view in views.values()}) == 1,
            "same_runtime": len({canonical(view["runtime"]) for view in views.values()}) == 1,
            "same_safety_state": len({canonical(c["safety_state"]) for c in contexts}) == 1,
            "same_shared_context": len({canonical(c["shared_context"]) for c in contexts}) == 1,
            "prior_fact_set_variants": len({canonical(facts) for facts in fact_sets}),
            "prior_action_variants": len({canonical(action) for action in actions}),
            "explicit_scaffold_history": isinstance(raw["initial_state"].get("scaffold_state"), dict),
            # Aliased action containers may be normalized, so this is not a loss detector.
            "initial_keys_not_copied_verbatim": sorted(set(raw["initial_state"]) - set().union(*(set(c) for c in contexts))),
            "expected_activation": expected,
            "assessment_policies": raw.get("applicable_support_policies", []),
            "declared_active_policies": raw["initial_state"].get("active_policies"),
            "expected_observables": raw["expected_observables"],
            "views": views,
        })
        if include_provenance:
            rows[-1]["context_provenance"] = inspect_shared_context(raw)
        rows[-1]["resolved_expectations"] = {
            name: {key: resolved[key] for key in ("expected_observables", "applicable_support_policies", "forbidden_support_policies")}
            for name, config in configurations.items()
            for resolved in [resolve_expectations(raw, {
                Path(ref).name.split(".v", 1)[0] for ref in config.support_policy_refs})]
        }
    return {
        "evidence_class": "OFFLINE_DEVELOPMENT_INPUT_CENSUS_NOT_MODEL_EVIDENCE",
        "scenario_count": len(rows), "configuration_count": len(CONFIGURATIONS),
        "projected_input_count": len(rows) * len(CONFIGURATIONS),
        "fact_variant_scenarios": [r["scenario_id"] for r in rows if r["prior_fact_set_variants"] > 1],
        "action_variant_scenarios": [r["scenario_id"] for r in rows if r["prior_action_variants"] > 1],
        "activation_annotation_counts": {
            policy: {"positive": sum(r["expected_activation"][policy] is True for r in rows),
                     "negative": sum(r["expected_activation"][policy] is False for r in rows),
                     "unknown": sum(r["expected_activation"][policy] is None for r in rows)}
            for policy in POLICIES
        },
        "rows": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance", "--candidate", dest="provenance", action="store_true", help="Include author-only provenance for implemented shared context")
    args = parser.parse_args()
    print(json.dumps(audit_development_inputs(Path(__file__).resolve().parents[1],
                                             include_provenance=args.provenance), ensure_ascii=False, indent=2))
