"""Policy-independent initial conversational context, derived only from a whitelist.

Statements are scenario-authored inputs, not independently verified user facts.
The current message and model output never update this input snapshot.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


def project_shared_context(initial: Mapping[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {
        "context_version": "1.0.0", "objective": initial.get("objective_declared"),
        "prior_facts": [], "prior_action": None, "prior_plan": None,
    }
    aliases = [key for key in ("action_record", "pending_proposed_action", "pending_action",
                               "pol03_accepted_action") if key in initial]
    if len(aliases) > 1:
        raise ValueError("Ambiguous prior action aliases require author review")
    for key in ("scaffold_state", *aliases):
        container = initial.get(key)
        if container is None:
            continue
        if not isinstance(container, Mapping):
            raise ValueError("Prior context container must be an object")
        facts = container.get("grounded_facts", ())
        if not isinstance(facts, (list, tuple)) or not all(isinstance(f, str) for f in facts):
            raise ValueError("Prior facts must be a sequence of strings")
        for fact in facts:
            if fact not in context["prior_facts"]:
                context["prior_facts"].append(fact)
    if aliases and initial[aliases[0]] is not None:
        action = initial[aliases[0]]
        context["prior_action"] = {
            "text": action.get("action_text"), "status": action.get("action_status", "UNKNOWN"),
            "acceptance_evidence": action.get("explicit_user_evidence"),
        }
    if "previous_plan_in_progress" in initial:
        plan = initial["previous_plan_in_progress"]
        if not isinstance(plan, Mapping):
            raise ValueError("Prior plan must be an object")
        context["prior_plan"] = {key: plan.get(key) for key in ("cue", "action", "status")}
    # Reject malformed scenario metadata before an adapter can spend tokens.
    if context["objective"] is not None and not isinstance(context["objective"], str):
        raise ValueError("Prior objective must be a string or null")
    action = context["prior_action"]
    if action is not None:
        if not isinstance(action["text"], str) or not action["text"]:
            raise ValueError("Prior action text must be non-empty")
        if action["status"] not in ("ACTION_PROPOSED", "ACTION_ACCEPTED", "ACTION_REJECTED",
                                    "CLARIFICATION_PENDING", "UNKNOWN"):
            raise ValueError("Unknown prior action status")
        if action["acceptance_evidence"] is not None and not isinstance(action["acceptance_evidence"], str):
            raise ValueError("Prior action evidence must be a string or null")
    if context["prior_plan"] is not None and any(
        value is not None and not isinstance(value, str) for value in context["prior_plan"].values()
    ):
        raise ValueError("Prior plan fields must be strings or null")
    return copy.deepcopy(context)
