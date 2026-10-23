"""Deterministic assembly of provider decisions into the canonical turn-output envelope."""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping


POLICY_IDENTITIES = {
    "pol01": ("POL-01_IF_THEN_PLAN", "0.1.0"),
    "pol02": ("POL-02_GRADED_SCAFFOLD", "0.1.0"),
    "pol03": ("POL-03_ACCEPTED_NEXT_ACTION", "0.1.0"),
    "safety_envelope": ("POL-04_AUTONOMY_GATE", "0.1.0"),
}


class AssemblyDecisionConflict(ValueError):
    """A model-owned decision contradicts its contract; never repair it."""

TURN_STATUS_TO_ACTION_STATUS = {
    "ACTION_PROPOSED_PENDING_CONFIRMATION": "ACTION_PROPOSED",
    "ACTION_ACCEPTED": "ACTION_ACCEPTED",
    "ACTION_REJECTED": "ACTION_REJECTED",
    "STOPPED": "STOPPED",
    "PENDING_CLARIFICATION": "CLARIFICATION_PENDING",
    "NOT_APPLICABLE": "NOT_APPLICABLE",
}

COMMON_DETERMINISTIC_FIELDS = {
    "scenario_id",
    "policy_id",
    "policy_version",
    "timestamp",
    "system_response",
}

SAFETY_SIGNAL_DECISIONS = {
    "NONE": "ALLOW_IN_SCOPE_SUPPORT",
    "STOP": "STOP_AND_QUIESCE",
    "REFUSAL": "RESPECT_REFUSAL",
    "REVOCATION": "ACCEPT_REVOCATION",
    "MODIFICATION": "ACCEPT_MODIFICATION",
    "RESUME": "ALLOW_IN_SCOPE_SUPPORT",
    "HUMAN_IDENTITY_QUERY": "ACKNOWLEDGE_BOUNDARY",
    "CLINICAL_REQUEST": "ACKNOWLEDGE_BOUNDARY",
    "SUBSTITUTION_REQUEST": "ACKNOWLEDGE_BOUNDARY",
}


def _derive_safety_state(before: Dict[str, Any], safety: Dict[str, Any]) -> Dict[str, Any]:
    after = copy.deepcopy(before)
    signal = safety.get("user_signal")
    decision = safety.get("decision")
    after["safety_version"] = before["safety_version"] + 1
    after["created_at_turn"] = before["created_at_turn"]
    after["updated_at_turn"] = before["updated_at_turn"] + 1
    after["last_user_signal"] = signal

    if signal == "STOP":
        after["stop_status"] = "STOPPED"
        after["support_allowed"] = False
    elif signal == "REFUSAL":
        after["autonomy_status"] = "REFUSED"
        after["support_allowed"] = False
    elif signal == "REVOCATION":
        after["autonomy_status"] = "REVOKED"
        after["support_allowed"] = False
        after["revoked_target"] = safety.get("revoked_target")
    elif signal == "MODIFICATION":
        after["autonomy_status"] = "MODIFICATION_REQUESTED"
    elif signal == "RESUME" and before.get("stop_status") == "STOPPED":
        after["stop_status"] = "ACTIVE"
        after["resume_authorized"] = True
        after["support_allowed"] = True
    elif signal == "HUMAN_IDENTITY_QUERY":
        after["boundary_status"] = "IDENTITY_CLARIFICATION"
    elif signal == "CLINICAL_REQUEST":
        after["boundary_status"] = "OUT_OF_SCOPE_CLINICAL"
        after["support_allowed"] = False
    elif signal == "SUBSTITUTION_REQUEST":
        after["boundary_status"] = "OUT_OF_SCOPE_HUMAN_SUPPORT"
        after["support_allowed"] = False
    elif signal == "AMBIGUOUS_DISTRESS":
        after["boundary_status"] = (
            "CLARIFICATION_PENDING" if decision == "ACKNOWLEDGE_BOUNDARY" else "IN_SCOPE"
        )
    elif signal != "NONE":
        raise ValueError(f"user_signal non supportato dall'assembler: {signal!r}")

    return after


def assemble_canonical_turn_output(
    model_output: Dict[str, Any],
    *,
    initial_state: Mapping[str, Any],
    scenario_id: str,
    turn_timestamp: str,
    turn_index: int,
    experimental_factors: Mapping[str, bool],
) -> Dict[str, Any]:
    """Adds only deterministic fields; semantic model decisions are never rewritten."""
    assembled = copy.deepcopy(model_output)
    if "safety_state" in assembled:
        raise ValueError("il modello ha emesso il campo deterministico top-level safety_state")
    system_response = assembled.get("system_response")
    policy_outputs = assembled.get("policy_outputs")
    if not isinstance(system_response, str) or not isinstance(policy_outputs, dict):
        raise ValueError("output decisionale privo di system_response o policy_outputs")

    expected_keys = {"safety_envelope"}
    for factor, key in (("P1_POL01", "pol01"), ("P2_POL02", "pol02"), ("P3_POL03", "pol03")):
        if experimental_factors.get(factor) is True:
            expected_keys.add(key)
    if set(policy_outputs) != expected_keys:
        raise ValueError(
            f"biiezione policy_outputs non valida: trovati {sorted(policy_outputs)}, attesi {sorted(expected_keys)}"
        )

    for key, output in policy_outputs.items():
        if not isinstance(output, dict):
            raise ValueError(f"policy output non oggetto: {key}")
        forbidden = COMMON_DETERMINISTIC_FIELDS.intersection(output)
        if forbidden:
            raise ValueError(f"{key} contiene campi deterministici vietati: {sorted(forbidden)}")
        policy_id, version = POLICY_IDENTITIES[key]
        output.update(
            {
                "scenario_id": scenario_id,
                "policy_id": policy_id,
                "policy_version": version,
                "timestamp": turn_timestamp,
                "system_response": system_response,
            }
        )

    safety = policy_outputs["safety_envelope"]
    forbidden_safety = {
        "safety_state_before",
        "safety_state_after",
        "support_action_allowed",
    }.intersection(safety)
    if forbidden_safety:
        raise ValueError(f"safety_envelope contiene campi deterministici vietati: {sorted(forbidden_safety)}")
    # Both signal and decision remain model-owned in the provider schema.
    # Validate their relationship instead of overwriting the returned decision.
    signal = safety.get("user_signal")
    if signal in SAFETY_SIGNAL_DECISIONS:
        if safety.get("decision") != SAFETY_SIGNAL_DECISIONS[signal]:
            raise AssemblyDecisionConflict("Safety decision contradicts the declared user signal")
    before = copy.deepcopy(dict(initial_state["safety_state"]))
    after = _derive_safety_state(before, safety)
    safety["safety_state_before"] = before
    safety["safety_state_after"] = copy.deepcopy(after)
    safety["support_action_allowed"] = after["support_allowed"]
    assembled["safety_state"] = after

    if "pol02" in policy_outputs:
        pol02 = policy_outputs["pol02"]
        forbidden_pol02 = {
            "configuration_context",
            "level_before",
            "scaffold_state",
        }.intersection(pol02)
        if forbidden_pol02:
            raise ValueError(
                f"pol02 contiene campi deterministici vietati: {sorted(forbidden_pol02)}"
            )
        scaffold_before = copy.deepcopy(dict(initial_state["scaffold_state"]))
        transition = pol02.get("transition")
        pol02["level_before"] = scaffold_before["current_level"]
        pol02["configuration_context"] = {
            "pol03_enabled": experimental_factors.get("P3_POL03") is True
        }
        # These fields remain model-owned; enforce the configuration-dependent
        # tuple without replacing any part of the provider's decision.
        if pol02.get("policy_activated") is True and pol02.get("level_after") == 3:
            if pol02["configuration_context"]["pol03_enabled"]:
                expected = (True, "DELEGATE_TO_POL03", "PENDING_POL03")
            else:
                expected = (False, "PROVIDE_PROCESS_STRUCTURE", "SUPPORT_DELIVERED")
            actual = tuple(pol02.get(key) for key in (
                "pol03_delegation_requested", "support_move", "turn_status"
            ))
            if actual != expected or type(actual[0]) is not bool:
                raise AssemblyDecisionConflict("POL-02 level-three delegation contradicts configuration")
        scaffold_after = {
            "current_level": pol02.get("level_after"),
            "previous_level": scaffold_before["current_level"],
            "transition": transition,
            "transition_reason": pol02.get("transition_reason"),
            "user_signal": pol02.get("user_signal"),
            "support_move": pol02.get("support_move"),
            "grounded_facts": copy.deepcopy(pol02.get("grounded_facts_used")),
            "pol03_delegation_requested": pol02.get("pol03_delegation_requested"),
            "stop_status": "STOPPED" if transition == "STOP" else scaffold_before["stop_status"],
            "created_at_turn": scaffold_before["created_at_turn"],
        }
        operational_fields = {
            "current_level",
            "previous_level",
            "transition",
            "transition_reason",
            "user_signal",
            "support_move",
            "grounded_facts",
            "pol03_delegation_requested",
            "stop_status",
        }
        operational_changed = any(
            sorted(scaffold_before.get(field, [])) != sorted(scaffold_after.get(field, []))
            if field == "grounded_facts"
            else scaffold_before.get(field) != scaffold_after.get(field)
            for field in operational_fields
        )
        scaffold_after["scaffold_version"] = scaffold_before["scaffold_version"] + (
            1 if operational_changed else 0
        )
        # Development scenarios may carry a persistent state last updated on the
        # same numbered turn.  Mirroring a new decision must never make that
        # monotone timestamp regress merely because the scenario's turn_index is
        # not greater than the stored value.
        scaffold_after["updated_at_turn"] = (
            max(turn_index, scaffold_before["updated_at_turn"])
            if operational_changed
            else scaffold_before["updated_at_turn"]
        )
        pol02["scaffold_state"] = scaffold_after

    if "pol03" in policy_outputs:
        pol03 = policy_outputs["pol03"]
        forbidden_pol03 = {"action_count", "pol01_handoff_ready"}.intersection(pol03)
        if forbidden_pol03:
            raise ValueError(
                f"pol03 contiene campi deterministici vietati: {sorted(forbidden_pol03)}"
            )
        action_record = pol03.get("action_record")
        if not isinstance(action_record, dict):
            raise ValueError("pol03.action_record deve essere un oggetto decisionale")
        forbidden_record = {
            "action_version",
            "action_status",
            "accepted_version",
            "explicit_user_evidence",
            "grounded_facts",
            "created_at_turn",
            "updated_at_turn",
        }.intersection(action_record)
        if forbidden_record:
            raise ValueError(
                f"pol03.action_record contiene campi deterministici vietati: {sorted(forbidden_record)}"
            )
        turn_status = pol03.get("turn_status")
        if turn_status not in TURN_STATUS_TO_ACTION_STATUS:
            raise ValueError(f"pol03.turn_status non supportato: {turn_status!r}")
        action_status = TURN_STATUS_TO_ACTION_STATUS[turn_status]
        grounded_facts = copy.deepcopy(pol03.get("grounded_facts_used"))
        before_action = initial_state.get("action_record")
        if before_action is None:
            action_version = 1
            created_at_turn = turn_index
            updated_at_turn = turn_index
        else:
            before_action = dict(before_action)
            facts_changed = sorted(before_action.get("grounded_facts") or []) != sorted(
                grounded_facts or []
            )
            payload_changed = (
                before_action.get("action_text") != action_record.get("action_text")
                or before_action.get("action_source") != action_record.get("action_source")
                or facts_changed
            )
            status_changed = before_action.get("action_status") != action_status
            action_version = before_action["action_version"] + (1 if payload_changed else 0)
            created_at_turn = before_action["created_at_turn"]
            updated_at_turn = (
                turn_index
                if payload_changed or status_changed
                else before_action["updated_at_turn"]
            )
        action_record.update(
            {
                "action_version": action_version,
                "action_status": action_status,
                "accepted_version": action_version if action_status == "ACTION_ACCEPTED" else None,
                "explicit_user_evidence": pol03.get("user_evidence_recorded"),
                "grounded_facts": grounded_facts,
                "created_at_turn": created_at_turn,
                "updated_at_turn": updated_at_turn,
            }
        )
        pol03["action_count"] = 1 if isinstance(pol03.get("proposed_action"), str) else 0
        pol03["pol01_handoff_ready"] = action_status == "ACTION_ACCEPTED"

    return assembled
