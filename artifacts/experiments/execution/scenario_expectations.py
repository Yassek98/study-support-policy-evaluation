"""Resolve author-declared conditional annotations, never model-dependent labels."""
from collections.abc import Mapping
from typing import Any
from itertools import product

SUPPORT_POLICIES = {"POL-01_IF_THEN_PLAN", "POL-02_GRADED_SCAFFOLD", "POL-03_ACCEPTED_NEXT_ACTION"}


def resolve_expectations(scenario: Mapping[str, Any], active: set[str]) -> dict[str, Any]:
    if not active <= SUPPORT_POLICIES:
        raise ValueError("Unknown support policy in expectation selector")
    resolved = dict(scenario)
    matches = []
    for branch in scenario.get("conditional_expectations", ()):
        when = branch["when"]
        if not when or not set(when) <= SUPPORT_POLICIES or any(type(v) is not bool for v in when.values()):
            raise ValueError("Invalid conditional expectation selector")
        if all((policy in active) == enabled for policy, enabled in when.items()):
            matches.append(branch)
    if len(matches) > 1:
        raise ValueError("Overlapping conditional expectations")
    if matches:
        for key in ("expected_observables", "applicable_support_policies", "forbidden_support_policies"):
            resolved[key] = list(matches[0][key])
    if set(resolved.get("applicable_support_policies", ())) & set(resolved.get("forbidden_support_policies", ())):
        raise ValueError("Conflicting conditional assessment annotations")
    return resolved


def validate_conditional_expectations(scenario: Mapping[str, Any]) -> None:
    try:
        from validation.checks.fixtures import parse_and_validate_observable_syntax
    except ImportError:
        from artifacts.experiments.validation.checks.fixtures import parse_and_validate_observable_syntax
    policies = sorted(SUPPORT_POLICIES)
    for enabled in product((False, True), repeat=3):
        resolved = resolve_expectations(scenario, {p for p, on in zip(policies, enabled) if on})
        for expression in resolved["expected_observables"]:
            valid, error, _ = parse_and_validate_observable_syntax(expression, scenario["evaluated_policy"])
            if not valid:
                raise ValueError(f"Invalid conditional observable: {error}")
