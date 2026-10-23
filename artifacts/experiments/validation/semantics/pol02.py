"""
Funzioni pure di validazione semantica per POL-02.
"""
from typing import Any
from .common import is_valid_iso8601, validate_scaffold_state_semantics

ALLOWED_NON_ACTIVATION_REASONS = {
    "USER_ALREADY_AUTONOMOUS_PLAN",
    "CONTENT_REQUEST_OUT_OF_SCOPE",
    "USER_REFUSED_SUPPORT",
    "MISSING_OBSERVABLE_SIGNAL"
}

NON_ACTIVATION_COMPATIBILITY = {
    "USER_ALREADY_AUTONOMOUS_WITH_CLEAR_PLAN": {
        "allowed_signals": {"NO_ASSISTANCE_REQUESTED", "EXPLICIT_SELF_DIRECTION", "AUTONOMY_RESUMED"},
        "allowed_transition_reasons": {"USER_ALREADY_AUTONOMOUS_PLAN"}
    },
    "USER_ALREADY_AUTONOMOUS_PLAN": {
        "allowed_signals": {"NO_ASSISTANCE_REQUESTED", "EXPLICIT_SELF_DIRECTION", "AUTONOMY_RESUMED"},
        "allowed_transition_reasons": {"USER_ALREADY_AUTONOMOUS_PLAN"}
    },
    "content_request_disciplinary_tutoring": {
        "allowed_signals": {"CONTENT_QUESTION"},
        "allowed_transition_reasons": {"CONTENT_REQUEST_OUT_OF_SCOPE"}
    },
    "CONTENT_REQUEST_OUT_OF_SCOPE": {
        "allowed_signals": {"CONTENT_QUESTION"},
        "allowed_transition_reasons": {"CONTENT_REQUEST_OUT_OF_SCOPE"}
    },
    "USER_REFUSED_SUPPORT": {
        "allowed_signals": {"STOP_REQUEST", "REFUSAL"},
        "allowed_transition_reasons": {"USER_REFUSED_SUPPORT"}
    },
    "MISSING_OBSERVABLE_SIGNAL": {
        "allowed_signals": {"INSUFFICIENT_EVIDENCE", "NO_ASSISTANCE_REQUESTED"},
        "allowed_transition_reasons": {"MISSING_OBSERVABLE_SIGNAL"}
    }
}

def validate_pol02_output_semantics(data, source_label="pol02_output"):
    errs = []
    # Divieto di campi di action_record in POL-02
    if "action_record" in data or "proposed_action" in data:
        errs.append(f"{source_label}: POL-02 non puo contenere direttamente 'action_record' o 'proposed_action'")

    # Timestamp ISO-8601 calendar validity check
    ts = data.get("timestamp")
    if ts and not is_valid_iso8601(ts):
        errs.append(f"{source_label}: timestamp non conforme allo standard ISO-8601 o valori di calendario impossibili ('{ts}')")

    sc_state = data.get("scaffold_state", {})
    errs.extend(validate_scaffold_state_semantics(sc_state, f"{source_label}.scaffold_state"))

    # Coerenza biunivoca rigorosa tra campi di livello superiore e scaffold_state
    if sc_state:
        if data.get("level_after") != sc_state.get("current_level"):
            errs.append(f"{source_label}: disallineamento level_after ({data.get('level_after')}) != scaffold_state.current_level ({sc_state.get('current_level')})")
        if data.get("level_before") != sc_state.get("previous_level"):
            errs.append(f"{source_label}: disallineamento level_before ({data.get('level_before')}) != scaffold_state.previous_level ({sc_state.get('previous_level')})")
        if data.get("transition") != sc_state.get("transition"):
            errs.append(f"{source_label}: disallineamento transition ({data.get('transition')}) != scaffold_state.transition ({sc_state.get('transition')})")
        if data.get("transition_reason") != sc_state.get("transition_reason"):
            errs.append(f"{source_label}: disallineamento transition_reason ({data.get('transition_reason')}) != scaffold_state.transition_reason ({sc_state.get('transition_reason')})")
        if data.get("user_signal") != sc_state.get("user_signal"):
            errs.append(f"{source_label}: disallineamento user_signal ({data.get('user_signal')}) != scaffold_state.user_signal ({sc_state.get('user_signal')})")
        if data.get("support_move") != sc_state.get("support_move"):
            errs.append(f"{source_label}: disallineamento support_move ({data.get('support_move')}) != scaffold_state.support_move ({sc_state.get('support_move')})")
        if data.get("pol03_delegation_requested") != sc_state.get("pol03_delegation_requested"):
            errs.append(f"{source_label}: disallineamento pol03_delegation_requested ({data.get('pol03_delegation_requested')}) != scaffold_state.pol03_delegation_requested ({sc_state.get('pol03_delegation_requested')})")
        if data.get("grounded_facts_used") != sc_state.get("grounded_facts"):
            errs.append(f"{source_label}: disallineamento grounded_facts_used ({data.get('grounded_facts_used')}) != scaffold_state.grounded_facts ({sc_state.get('grounded_facts')})")

    lvl_before = data.get("level_before")
    lvl_after = data.get("level_after")
    trans = data.get("transition")
    trans_reason = data.get("transition_reason")
    user_sig = data.get("user_signal")
    sup_move = data.get("support_move")
    pol03_del = data.get("pol03_delegation_requested")
    turn_st = data.get("turn_status")
    act = data.get("policy_activated")
    non_act_r = data.get("non_activation_reason")
    orient_q = data.get("orientation_question")
    dec_opts = data.get("declared_options_used", [])
    grounded_f = data.get("grounded_facts_used", [])

    # Applicabilita e stato del turno
    if act is False:
        if not non_act_r:
            errs.append(f"{source_label}: policy_activated=false richiede non_activation_reason non nullo")
        if turn_st != "NOT_APPLICABLE":
            errs.append(f"{source_label}: policy_activated=false richiede turn_status=NOT_APPLICABLE (trovato {turn_st})")
        if sup_move != "NONE":
            errs.append(f"{source_label}: policy_activated=false richiede support_move=NONE (trovato {sup_move})")
        if trans != "NOT_APPLICABLE":
            errs.append(f"{source_label}: policy_activated=false richiede transition=NOT_APPLICABLE (trovato {trans})")
        if trans_reason not in ALLOWED_NON_ACTIVATION_REASONS:
            errs.append(f"{source_label}: policy_activated=false ha transition_reason incompatibile '{trans_reason}'")
        if non_act_r in NON_ACTIVATION_COMPATIBILITY:
            compat = NON_ACTIVATION_COMPATIBILITY[non_act_r]
            if user_sig not in compat["allowed_signals"]:
                errs.append(f"{source_label}: non_activation_reason '{non_act_r}' incompatibile con user_signal '{user_sig}'")
            if trans_reason not in compat["allowed_transition_reasons"]:
                errs.append(f"{source_label}: non_activation_reason '{non_act_r}' incompatibile con transition_reason '{trans_reason}'")
    else:
        if non_act_r is not None:
            errs.append(f"{source_label}: policy_activated=true richiede non_activation_reason=null")
        if turn_st == "NOT_APPLICABLE":
            errs.append(f"{source_label}: policy_activated=true non puo avere turn_status=NOT_APPLICABLE")
        if trans == "NOT_APPLICABLE":
            errs.append(f"{source_label}: policy_activated=true non puo avere transition=NOT_APPLICABLE")
        if sup_move == "NONE":
            errs.append(f"{source_label}: policy_activated=true richiede support_move != NONE")

    # 1. Rilevazione violazioni di sicurezza esplicite
    for sv in data.get("safety_violations", []):
        errs.append(f"{source_label}: violazione registrata: {sv.get('rule_id')} ({sv.get('description')})")

    # Clinical-language detection is centralized in safety.py, where positive
    # assertions are distinguished from boundary statements such as "I cannot
    # diagnose".  A second substring detector here produced false positives on
    # compliant trace explanations and is intentionally not duplicated.

    # 3. Confine disciplinare
    if user_sig == "CONTENT_QUESTION" and act is True:
        errs.append(f"{source_label}: policy attivata indebitamente su richiesta di contenuto disciplinare (user_signal=CONTENT_QUESTION)")

    # 4. Invarianti di transizione e algebra dei livelli
    if trans == "HOLD":
        if lvl_before is not None and lvl_after != lvl_before:
            errs.append(f"{source_label}: transizione HOLD richiede level_after ({lvl_after}) == level_before ({lvl_before})")

    if trans == "ESCALATE":
        if lvl_before is not None and lvl_after <= lvl_before:
            errs.append(f"{source_label}: transizione ESCALATE richiede level_after ({lvl_after}) > level_before ({lvl_before})")
        if lvl_before == 3:
            errs.append(f"{source_label}: transizione ESCALATE non permessa da livello 3")
        allowed_escalate_signals = {"PERSISTENT_STALL", "MORE_HELP_REQUESTED", "ORIENTATION_REQUEST", "INSUFFICIENT_EVIDENCE"}
        if user_sig not in allowed_escalate_signals:
            errs.append(f"{source_label}: transizione ESCALATE non ammessa con user_signal={user_sig}")
        if lvl_before is not None and (lvl_after - lvl_before) > 1:
            if trans_reason != "USER_REQUESTED_MORE_STRUCTURE" or user_sig != "MORE_HELP_REQUESTED":
                errs.append(f"{source_label}: salto di livello ingiustificato ({lvl_before} -> {lvl_after}) con segnale {user_sig}")

    if trans == "FADE":
        if lvl_before is not None and lvl_after >= lvl_before:
            errs.append(f"{source_label}: transizione FADE richiede level_after ({lvl_after}) < level_before ({lvl_before})")
        if lvl_before == 0:
            errs.append(f"{source_label}: transizione FADE non permessa da livello 0")

    if trans == "RESET_TO_AUTONOMY":
        if lvl_after != 0:
            errs.append(f"{source_label}: transizione RESET_TO_AUTONOMY richiede level_after=0 (trovato {lvl_after})")
        if sup_move != "AUTONOMY_INVITATION":
            errs.append(f"{source_label}: transizione RESET_TO_AUTONOMY richiede support_move=AUTONOMY_INVITATION (trovato {sup_move})")

    if trans == "STOP":
        if lvl_after != 0 or turn_st != "STOPPED" or pol03_del is not False:
            errs.append(f"{source_label}: transizione STOP richiede level_after=0, turn_status=STOPPED e pol03_delegation_requested=false")
        if sup_move != "STOP_AND_ACKNOWLEDGE":
            errs.append(f"{source_label}: transizione STOP richiede support_move=STOP_AND_ACKNOWLEDGE (trovato {sup_move})")
        if sc_state.get("stop_status") != "STOPPED":
            errs.append(f"{source_label}: transizione STOP richiede scaffold_state.stop_status=STOPPED")

    # 5. Rilevazione mancato fading o mancato stop
    if user_sig in ["AUTONOMY_RESUMED", "EXPLICIT_SELF_DIRECTION"] and lvl_before is not None and lvl_before > 0:
        if trans not in ["FADE", "RESET_TO_AUTONOMY"]:
            errs.append(f"{source_label}: mancato fading/reset a fronte di segnale di autonomia ({user_sig})")

    if user_sig in ["STOP_REQUEST", "REFUSAL"]:
        if trans != "STOP" or turn_st != "STOPPED" or lvl_after != 0:
            errs.append(f"{source_label}: mancato stop a fronte di segnale {user_sig}")

    # 6. Livello 3 ingiustificato
    if lvl_after == 3 and user_sig not in ["MORE_HELP_REQUESTED", "PERSISTENT_STALL"]:
        errs.append(f"{source_label}: livello 3 applicato senza stallo o richiesta di struttura (user_signal={user_sig})")

    # 7. Coerenza tra user_signal e transition_reason
    if user_sig == "STOP_REQUEST" and trans_reason not in ["USER_REFUSED_SUPPORT", "MISSING_OBSERVABLE_SIGNAL"]:
        errs.append(f"{source_label}: user_signal=STOP_REQUEST incompatibile con transition_reason={trans_reason}")
    if user_sig == "EXPLICIT_SELF_DIRECTION" and trans == "ESCALATE":
        errs.append(f"{source_label}: user_signal=EXPLICIT_SELF_DIRECTION non puo produrre ESCALATE")

    # 8. Invariante domande orientative al Livello 1 (euristica conteggio '?')
    if lvl_after == 1 and act is True:
        if not orient_q:
            errs.append(f"{source_label}: level_after=1 richiede orientation_question non vuota")
        elif orient_q.count("?") > 1:
            errs.append(f"{source_label}: level_after=1 contiene piu di una domanda orientativa ({orient_q.count('?')} '?')")
    else:
        if orient_q is not None:
            errs.append(f"{source_label}: level_after!=1 richiede orientation_question=null")

    # 9. Invariante opzioni dichiarate al Livello 2
    if lvl_after == 2:
        if len(dec_opts) > 2:
            errs.append(f"{source_label}: level_after=2 ammette al massimo 2 opzioni dichiarate (trovate {len(dec_opts)})")
        for opt in dec_opts:
            if opt not in grounded_f and not any(f.lower() in opt.lower() or opt.lower() in f.lower() for f in grounded_f):
                errs.append(f"{source_label}: opzione '{opt}' non presente nei grounded_facts_used ({grounded_f})")

    # 10. Invariante Livello 3 e delega POL-03
    config_ctx = data.get("configuration_context")
    if lvl_after == 3 and act is True:
        if not config_ctx or not isinstance(config_ctx, dict) or "pol03_enabled" not in config_ctx:
            errs.append(f"{source_label}: level_after=3 richiede obbligatoriamente configuration_context con campo pol03_enabled booleano")
        else:
            pol03_en = config_ctx.get("pol03_enabled")
            if not isinstance(pol03_en, bool):
                errs.append(f"{source_label}: configuration_context.pol03_enabled deve essere booleano (trovato {pol03_en})")
            elif pol03_en is True:
                if pol03_del is not True:
                    errs.append(f"{source_label}: level_after=3 con pol03_enabled=true richiede pol03_delegation_requested=true")
                if sup_move != "DELEGATE_TO_POL03":
                    errs.append(f"{source_label}: level_after=3 con pol03_enabled=true richiede support_move=DELEGATE_TO_POL03 (trovato {sup_move})")
                if turn_st != "PENDING_POL03":
                    errs.append(f"{source_label}: level_after=3 con pol03_enabled=true richiede turn_status=PENDING_POL03 (trovato {turn_st})")
            elif pol03_en is False:
                if pol03_del is not False:
                    errs.append(f"{source_label}: level_after=3 con pol03_enabled=false richiede pol03_delegation_requested=false")
                if sup_move != "PROVIDE_PROCESS_STRUCTURE":
                    errs.append(f"{source_label}: level_after=3 con pol03_enabled=false richiede support_move=PROVIDE_PROCESS_STRUCTURE (trovato {sup_move})")
                if turn_st == "PENDING_POL03":
                    errs.append(f"{source_label}: level_after=3 con pol03_enabled=false non puo avere turn_status=PENDING_POL03")

    if (turn_st == "PENDING_POL03") != (pol03_del is True):
        errs.append(f"{source_label}: equivalenza violata: turn_status=PENDING_POL03 deve coincidere con pol03_delegation_requested=true (turn_status={turn_st}, pol03_delegation_requested={pol03_del})")

    if pol03_del is True:
        if not config_ctx or not isinstance(config_ctx, dict):
            errs.append(f"{source_label}: pol03_delegation_requested=true richiede la presenza di configuration_context")
        elif config_ctx.get("pol03_enabled") is not True:
            errs.append(f"{source_label}: pol03_delegation_requested=true richiede configuration_context.pol03_enabled=true (trovato {config_ctx.get('pol03_enabled')})")
        if lvl_after != 3:
            errs.append(f"{source_label}: pol03_delegation_requested=true richiede level_after=3 (trovato {lvl_after})")
        if sup_move != "DELEGATE_TO_POL03":
            errs.append(f"{source_label}: pol03_delegation_requested=true richiede support_move=DELEGATE_TO_POL03 (trovato {sup_move})")

    if config_ctx and config_ctx.get("pol03_enabled") is False and pol03_del is True:
        errs.append(f"{source_label}: delega a POL-03 richiesta ma POL-03 e' disattivata in configuration_context (pol03_enabled=false)")

    return errs
