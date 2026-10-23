"""
Funzioni pure di validazione semantica per POL-03.
"""
from typing import Any
from .common import is_valid_iso8601, validate_action_record_semantics

def validate_pol03_output_semantics(data, source_label="pol03_output"):
    errs = []
    # Timestamp ISO-8601 calendar validity check
    ts = data.get("timestamp")
    if ts and not is_valid_iso8601(ts):
        errs.append(f"{source_label}: timestamp non conforme allo standard ISO-8601 o valori di calendario impossibili ('{ts}')")

    rec = data.get("action_record", {})
    errs.extend(validate_action_record_semantics(rec, f"{source_label}.record"))

    prop_act = data.get("proposed_action")
    rec_act = rec.get("action_text")
    cnt = data.get("action_count")

    # Cardinalita
    if prop_act is not None:
        if cnt != 1:
            errs.append(f"{source_label}: proposed_action presente richiede action_count=1 (trovato {cnt})")
        if rec_act is not None and prop_act != rec_act:
            errs.append(f"{source_label}: proposed_action non coincide con action_record.action_text")
    else:
        if cnt != 0:
            errs.append(f"{source_label}: proposed_action=null richiede action_count=0 (trovato {cnt})")

    # Coerenza fatti di grounding
    top_facts = data.get("grounded_facts_used", [])
    rec_facts = rec.get("grounded_facts", [])
    if top_facts != rec_facts:
        errs.append(f"{source_label}: grounded_facts_used ({top_facts}) non coincide con action_record.grounded_facts ({rec_facts})")

    if rec.get("action_source") == "SYSTEM_PROPOSED_FROM_DECLARED_FACTS":
        if not top_facts or len(top_facts) == 0:
            errs.append(f"{source_label}: SYSTEM_PROPOSED_FROM_DECLARED_FACTS richiede grounded_facts_used non vuoto")

    # Coerenza evidenza utente
    top_ev = data.get("user_evidence_recorded")
    rec_ev = rec.get("explicit_user_evidence")
    if top_ev != rec_ev:
        errs.append(f"{source_label}: user_evidence_recorded ('{top_ev}') non coincide con action_record.explicit_user_evidence ('{rec_ev}')")

    # Coerenza chiarimento
    clar_req = data.get("clarification_requested")
    clar_prompt = data.get("clarification_prompt")
    turn_st = data.get("turn_status")
    rec_st = rec.get("action_status")

    if clar_req is True:
        if not clar_prompt:
            errs.append(f"{source_label}: clarification_requested=true richiede clarification_prompt non vuoto")
        if turn_st != "PENDING_CLARIFICATION":
            errs.append(f"{source_label}: clarification_requested=true richiede turn_status=PENDING_CLARIFICATION (trovato {turn_st})")
    else:
        if clar_prompt is not None:
            errs.append(f"{source_label}: clarification_requested=false richiede clarification_prompt=null (trovato '{clar_prompt}')")
        if turn_st == "PENDING_CLARIFICATION":
            errs.append(f"{source_label}: turn_status=PENDING_CLARIFICATION richiede clarification_requested=true")

    # Corrispondenza biunivoca stati operativi
    status_map = {
        "ACTION_PROPOSED_PENDING_CONFIRMATION": "ACTION_PROPOSED",
        "ACTION_ACCEPTED": "ACTION_ACCEPTED",
        "ACTION_REJECTED": "ACTION_REJECTED",
        "STOPPED": "STOPPED",
        "PENDING_CLARIFICATION": "CLARIFICATION_PENDING",
        "NOT_APPLICABLE": "NOT_APPLICABLE"
    }
    expected_rec_st = status_map.get(turn_st)
    if expected_rec_st and rec_st != expected_rec_st:
        errs.append(f"{source_label}: incongruenza tra turn_status ({turn_st}) e action_record.action_status ({rec_st}, atteso {expected_rec_st})")

    # Applicabilita: NOT_APPLICABLE e attivazione sono mutuamente esclusivi.
    policy_activated = data.get("policy_activated")
    if turn_st == "NOT_APPLICABLE" and policy_activated is not False:
        errs.append(f"{source_label}: turn_status=NOT_APPLICABLE richiede policy_activated=false")
    if policy_activated is False and turn_st != "NOT_APPLICABLE":
        errs.append(f"{source_label}: policy_activated=false richiede turn_status=NOT_APPLICABLE (trovato {turn_st})")

    # Invariante di handoff
    handoff = data.get("pol01_handoff_ready")
    if handoff is True:
        if turn_st != "ACTION_ACCEPTED" or rec_st != "ACTION_ACCEPTED":
            errs.append(f"{source_label}: pol01_handoff_ready=true ma turn_status={turn_st} o record_status={rec_st}")
    else:
        if turn_st == "ACTION_ACCEPTED" and rec_st == "ACTION_ACCEPTED":
            errs.append(f"{source_label}: stato ACTION_ACCEPTED ma pol01_handoff_ready=false (violazione bi-direzionale iff)")

    return errs
