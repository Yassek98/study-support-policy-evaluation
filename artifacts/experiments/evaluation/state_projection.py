"""Common-state rater projection; support-specific representations are withheld."""
from __future__ import annotations

from typing import Any, Literal


NORMALIZED_STATE_FIELDS = (
    "session_control",
    "plan_commitment",
    "action_commitment",
    "clarification_status",
    "support_permission",
)

ALLOWED_VALUES = {
    "session_control": {"ACTIVE", "STOPPED", "REFUSED", "REVOKED", "MODIFICATION_REQUESTED"},
    "plan_commitment": {"NOT_OBSERVED"},
    "action_commitment": {"NOT_OBSERVED"},
    "clarification_status": {"NOT_OBSERVED"},
    "support_permission": {"ALLOWED", "BLOCKED"},
}


def _state_container(record: dict[str, Any], phase: Literal["before", "after"]) -> dict[str, Any]:
    key = "initial_state" if phase == "before" else "final_state"
    value = record.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Canonical run record has no object field {key!r}")
    return value


def _session_control(state: dict[str, Any]) -> str:
    safety = state.get("safety_state", {})
    if not isinstance(safety, dict):
        raise ValueError("Canonical state has no safety_state object")
    if safety.get("stop_status") == "STOPPED" or safety.get("last_user_signal") == "STOP":
        return "STOPPED"
    return {
        "REFUSED": "REFUSED",
        "REVOKED": "REVOKED",
        "MODIFICATION_REQUESTED": "MODIFICATION_REQUESTED",
    }.get(safety.get("autonomy_status"), "ACTIVE")


def _support_permission(state: dict[str, Any]) -> str:
    safety = state.get("safety_state", {})
    if not isinstance(safety, dict):
        raise ValueError("Canonical state has no safety_state object")
    return "ALLOWED" if safety.get("support_allowed") is True else "BLOCKED"


def project_state(
    record: dict[str, Any],
    phase: Literal["before", "after"] = "after",
) -> dict[str, str]:
    """Project exactly five neutral fields from a canonical run record."""
    state = _state_container(record, phase)
    projected = {
        "session_control": _session_control(state),
        # Identical masking in every treatment: missing software fields must
        # never be interpreted as absent conversational commitments. Q5 uses
        # the common safety state and supplied prior context, not policy state.
        "plan_commitment": "NOT_OBSERVED",
        "action_commitment": "NOT_OBSERVED",
        "clarification_status": "NOT_OBSERVED",
        "support_permission": _support_permission(state),
    }
    errors = validate_normalized_state(projected)
    if errors:
        raise ValueError("Invalid NormalizedState projection: " + "; ".join(errors))
    return projected


def validate_normalized_state(state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    unknown = set(state) - set(NORMALIZED_STATE_FIELDS)
    if unknown:
        errors.append(f"unexpected fields: {sorted(unknown)}")
    for field in NORMALIZED_STATE_FIELDS:
        if field not in state:
            errors.append(f"missing field: {field}")
        elif state[field] not in ALLOWED_VALUES[field]:
            errors.append(f"{field} has invalid value {state[field]!r}")
    return errors


def states_are_structurally_identical(states: list[dict[str, Any]]) -> bool:
    expected = set(NORMALIZED_STATE_FIELDS)
    return bool(states) and all(set(state) == expected for state in states)
