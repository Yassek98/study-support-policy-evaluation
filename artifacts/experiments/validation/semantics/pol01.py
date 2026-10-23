"""Funzioni pure di validazione semantica per POL-01."""
from .common import is_valid_iso8601


def validate_pol01_output_semantics(data, source_label="pol01_output"):
    """Verifica le invarianti operative che il solo JSON Schema non esprime."""
    errs = []

    ts = data.get("timestamp")
    if ts and not is_valid_iso8601(ts):
        errs.append(
            f"{source_label}: timestamp non conforme allo standard ISO-8601 "
            f"o con valori di calendario impossibili ('{ts}')"
        )

    active = data.get("policy_activated")
    reason = data.get("non_activation_reason")
    cue = data.get("cue") or {}
    action = data.get("action") or {}
    plan = data.get("plan") or {}
    missing = data.get("missing_elements") or []
    clarification = data.get("clarification_requested")

    if len(missing) != len(set(missing)):
        errs.append(f"{source_label}: missing_elements contiene duplicati")

    for violation in data.get("violations", []):
        errs.append(
            f"{source_label}: violazione registrata: "
            f"{violation.get('forbidden_action')} ({violation.get('severity')})"
        )

    if active is False:
        if not reason:
            errs.append(
                f"{source_label}: policy_activated=false richiede "
                "non_activation_reason non nullo"
            )
        if clarification is True:
            errs.append(
                f"{source_label}: policy non attiva non puo richiedere chiarimenti"
            )
        if plan.get("formulated") is not None:
            errs.append(
                f"{source_label}: policy non attiva non puo formulare un piano"
            )
        status = plan.get("confirmation_status")
        if reason in ("user_refused_or_stopped", "user_refused", "user_stopped"):
            if plan and status not in ("rejected", "not_reached", None):
                errs.append(
                    f"{source_label}: stop o rifiuto richiede "
                    "confirmation_status=rejected o not_reached quando il record plan e presente"
                )
        elif status not in (None, "not_reached", "rejected"):
            errs.append(
                f"{source_label}: non-attivazione ordinaria non puo avere "
                f"confirmation_status={status}"
            )
        return errs

    if reason is not None:
        errs.append(
            f"{source_label}: policy_activated=true richiede non_activation_reason=null"
        )

    cue_specificity = cue.get("specificity")
    cue_source = cue.get("source")
    cue_identified = cue.get("identified")
    action_observability = action.get("observability")
    action_source = action.get("source")
    action_identified = action.get("identified")
    formulated = plan.get("formulated")
    confirmation_status = plan.get("confirmation_status")
    user_response = plan.get("user_response")

    if cue_source == "missing" and cue_identified is not None:
        errs.append(f"{source_label}: cue.source=missing richiede cue.identified=null")
    if cue_specificity == "missing" and cue_identified is not None:
        errs.append(
            f"{source_label}: cue.specificity=missing richiede cue.identified=null"
        )
    if action_source == "missing" and action_identified is not None:
        errs.append(
            f"{source_label}: action.source=missing richiede action.identified=null"
        )
    if action_observability == "missing" and action_identified is not None:
        errs.append(
            f"{source_label}: action.observability=missing richiede action.identified=null"
        )
    if action_source == "system_invented":
        errs.append(
            f"{source_label}: azione inventata dal sistema vietata "
            "(action.source=system_invented)"
        )

    cue_incomplete = cue_specificity in {"vague", "missing"}
    action_incomplete = action_observability in {"vague", "missing"}

    if cue_incomplete:
        if "cue" not in missing:
            errs.append(
                f"{source_label}: cue {cue_specificity} richiede 'cue' in missing_elements"
            )
        if clarification is not True:
            errs.append(
                f"{source_label}: cue {cue_specificity} richiede clarification_requested=true"
            )
    if action_incomplete:
        if "action" not in missing:
            errs.append(
                f"{source_label}: action {action_observability} richiede "
                "'action' in missing_elements"
            )
        if clarification is not True:
            errs.append(
                f"{source_label}: action {action_observability} richiede "
                "clarification_requested=true"
            )

    if cue_incomplete or action_incomplete:
        if formulated is not None:
            errs.append(
                f"{source_label}: cue o azione incompleti vietano plan.formulated"
            )
        if confirmation_status not in {"pending", "not_reached"}:
            errs.append(
                f"{source_label}: piano incompleto richiede confirmation_status "
                f"pending o not_reached (trovato {confirmation_status})"
            )
    elif cue_specificity == "specific" and action_observability == "observable":
        if "cue" in missing or "action" in missing:
            errs.append(
                f"{source_label}: cue e azione completi non possono risultare mancanti"
            )
        if clarification is not False:
            errs.append(
                f"{source_label}: cue e azione completi richiedono "
                "clarification_requested=false"
            )
        if not formulated:
            errs.append(
                f"{source_label}: cue specifico e azione osservabile richiedono "
                "plan.formulated non nullo"
            )

    if confirmation_status in {"confirmed", "modified", "rejected"}:
        if not isinstance(user_response, str) or not user_response.strip():
            errs.append(
                f"{source_label}: confirmation_status={confirmation_status} richiede "
                "user_response esplicita non vuota"
            )
    elif confirmation_status in {"pending", "not_reached"} and user_response is not None:
        errs.append(
            f"{source_label}: confirmation_status={confirmation_status} richiede "
            "user_response=null"
        )

    return errs
