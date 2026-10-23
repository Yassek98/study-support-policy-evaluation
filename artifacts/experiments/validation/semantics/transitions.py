"""
Funzioni pure di validazione delle transizioni di stato di traiettoria:
- validate_action_transition (action_record: before -> after)
- validate_scaffold_transition (scaffold_state: before -> after)

Vocabolari e invarianti rigorosamente sincronizzati con action_record.schema.json,
scaffold_state.schema.json e pol02.py.
"""

from typing import Any, Dict, List, Optional, Set

# 1. Vocabolario canonico action_record.schema.json
ALLOWED_ACTION_STATUSES: Set[str] = {
    "CLARIFICATION_PENDING",
    "ACTION_PROPOSED",
    "ACTION_ACCEPTED",
    "ACTION_REJECTED",
    "STOPPED",
    "NOT_APPLICABLE",
}

# Tabella esplicita e autoritativa delle transizioni di status ammesse per action_record
ALLOWED_ACTION_STATUS_TRANSITIONS: Dict[str, Set[str]] = {
    "CLARIFICATION_PENDING": {
        "CLARIFICATION_PENDING",
        "ACTION_PROPOSED",
        "ACTION_REJECTED",
        "STOPPED",
        "NOT_APPLICABLE",
    },
    "ACTION_PROPOSED": {
        "ACTION_PROPOSED",
        "ACTION_ACCEPTED",
        "ACTION_REJECTED",
        "STOPPED",
        "CLARIFICATION_PENDING",
        "NOT_APPLICABLE",
    },
    "ACTION_ACCEPTED": {
        "ACTION_ACCEPTED",
        "ACTION_PROPOSED",
        "ACTION_REJECTED",
        "STOPPED",
        "NOT_APPLICABLE",
    },
    "ACTION_REJECTED": {
        "ACTION_REJECTED",
        "ACTION_PROPOSED",
        "STOPPED",
        "NOT_APPLICABLE",
    },
    "STOPPED": {
        "STOPPED",
        "ACTION_PROPOSED",
        "NOT_APPLICABLE",
    },
    "NOT_APPLICABLE": {
        "NOT_APPLICABLE",
        "CLARIFICATION_PENDING",
        "ACTION_PROPOSED",
        "STOPPED",
    },
}


def validate_action_transition(
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
    turn: int,
    user_message: Optional[str] = None,
    source_label: str = "action_trajectory",
) -> List[str]:
    """
    Valida la transizione di action_record da initial_state a final_state.
    Supporta:
    - assenza continua di azione (before is None e after is None)
    - creazione iniziale dell'azione nel turno corrente (before is None e after is not None)
    - transizione standard tra record di azione esistenti (before is not None e after is not None)
    """
    errs: List[str] = []

    # Caso A: Nessuna azione prima e nessuna azione dopo
    if before is None and after is None:
        return errs

    # Caso B: Azione cancellata (illegale)
    if before is not None and after is None:
        errs.append(f"{source_label}: action_record non puo essere distrutta o resettata a null dopo essere stata creata")
        return errs

    # Caso C: Creazione iniziale dell'action_record durante il turno (before is None -> after is not None)
    if before is None and after is not None:
        act_id = after.get("action_id")
        if not act_id:
            errs.append(f"{source_label}: nuova action_record priva di action_id")
        act_ver = after.get("action_version")
        if act_ver != 1:
            errs.append(f"{source_label}: nuova action_record deve partire da action_version=1 (trovato {act_ver})")
        c_turn = after.get("created_at_turn")
        u_turn = after.get("updated_at_turn")
        if c_turn != turn:
            errs.append(f"{source_label}: nuova action_record creata al turno {turn} deve avere created_at_turn=={turn} (trovato {c_turn})")
        if u_turn != turn:
            errs.append(f"{source_label}: nuova action_record creata al turno {turn} deve avere updated_at_turn=={turn} (trovato {u_turn})")
        a_st = after.get("action_status")
        if a_st not in ALLOWED_ACTION_STATUSES:
            errs.append(f"{source_label}: action_status non valido '{a_st}' per nuova action_record")
        elif a_st not in {"ACTION_PROPOSED", "CLARIFICATION_PENDING", "NOT_APPLICABLE"}:
            errs.append(f"{source_label}: nuova action_record non puo nascere con status '{a_st}'")
        if after.get("accepted_version") is not None:
            errs.append(f"{source_label}: nuova action_record non accettata non puo avere accepted_version valorizzato")
        if after.get("explicit_user_evidence") is not None:
            errs.append(f"{source_label}: nuova action_record non accettata non puo avere explicit_user_evidence valorizzato")
        return errs

    # Caso D: Transizione standard tra record esistenti (before is not None e after is not None)
    # 1. Invarianza dei campi strutturali immutabili
    if after.get("action_id") != before.get("action_id"):
        errs.append(
            f"{source_label}: action_id mutato da '{before.get('action_id')}' a '{after.get('action_id')}'"
        )

    if after.get("created_at_turn") != before.get("created_at_turn"):
        errs.append(
            f"{source_label}: created_at_turn mutato da {before.get('created_at_turn')} a {after.get('created_at_turn')}"
        )

    # 2. Transizione legale di status
    b_st = before.get("action_status")
    a_st = after.get("action_status")

    if b_st not in ALLOWED_ACTION_STATUS_TRANSITIONS:
        errs.append(f"{source_label}: action_status precedente non valido: '{b_st}'")
    elif a_st not in ALLOWED_ACTION_STATUS_TRANSITIONS[b_st]:
        errs.append(
            f"{source_label}: transizione di action_status non consentita da '{b_st}' a '{a_st}'"
        )

    # 3. Payload semantico (action_text, action_source, grounded_facts)
    b_text = before.get("action_text")
    a_text = after.get("action_text")
    b_src = before.get("action_source")
    a_src = after.get("action_source")
    b_facts = before.get("grounded_facts")
    a_facts = after.get("grounded_facts")

    facts_changed = (
        (b_facts is None and a_facts is not None)
        or (b_facts is not None and a_facts is None)
        or (b_facts is not None and a_facts is not None and sorted(b_facts) != sorted(a_facts))
    )
    payload_changed = (b_text != a_text) or (b_src != a_src) or facts_changed

    # 4. Monotonicita e versioning
    b_ver = before.get("action_version", 1)
    a_ver = after.get("action_version", 1)
    b_up = before.get("updated_at_turn", 1)
    a_up = after.get("updated_at_turn", 1)

    # Monotonicita temporale
    if a_up > turn:
        errs.append(
            f"{source_label}: updated_at_turn ({a_up}) non puo essere futuro rispetto al turno corrente ({turn})"
        )
    if a_up < b_up:
        errs.append(f"{source_label}: updated_at_turn regredito da {b_up} a {a_up}")

    # Monotonicita di versione
    if a_ver < b_ver:
        errs.append(f"{source_label}: action_version regredita da {b_ver} a {a_ver}")
    elif a_ver > b_ver + 1:
        errs.append(
            f"{source_label}: salto di action_version non consentito da {b_ver} a {a_ver} (consentito solo +1 o invarianza)"
        )

    # Regole versioning vs payload
    if payload_changed:
        if a_ver != b_ver + 1:
            errs.append(
                f"{source_label}: modifica del payload semantico richiede incremento esatto di action_version (+1, atteso {b_ver + 1}, trovato {a_ver})"
            )
        if a_up != turn:
            errs.append(
                f"{source_label}: modifica del payload semantico richiede updated_at_turn == {turn} (trovato {a_up})"
            )
    else:
        # Payload invariato: versione DEVE rimanere identica
        if a_ver != b_ver:
            errs.append(
                f"{source_label}: incremento di action_version ({b_ver} -> {a_ver}) senza alcuna modifica del payload semantico"
            )
        if a_st != b_st:
            # Cambio di status operativo richiede aggiornamento del turno
            if a_up != turn:
                errs.append(
                    f"{source_label}: transizione di status ({b_st} -> {a_st}) richiede updated_at_turn == {turn} (trovato {a_up})"
                )
        else:
            # Ne payload ne status mutati: updated_at_turn deve rimanere identico
            if a_up != b_up:
                errs.append(
                    f"{source_label}: updated_at_turn mutato da {b_up} a {a_up} senza alcuna modifica di payload o status"
                )

    # 5. Regole per ACTION_ACCEPTED
    if a_st == "ACTION_ACCEPTED":
        acc_ver = after.get("accepted_version")
        if acc_ver != a_ver:
            errs.append(
                f"{source_label}: accepted_version ({acc_ver}) deve coincidere con action_version ({a_ver}) su ACTION_ACCEPTED"
            )
        evidence = after.get("explicit_user_evidence")
        if not evidence or not isinstance(evidence, str) or not evidence.strip():
            errs.append(f"{source_label}: explicit_user_evidence deve essere una stringa non vuota su ACTION_ACCEPTED")
        elif user_message and evidence.lower() not in user_message.lower():
            errs.append(
                f"{source_label}: explicit_user_evidence ('{evidence}') non trovata verbatim nel messaggio utente"
            )
    else:
        # Qualsiasi status diverso da ACTION_ACCEPTED richiede azzeramento di accepted_version ed explicit_user_evidence
        if after.get("accepted_version") is not None:
            errs.append(
                f"{source_label}: status '{a_st}' richiede accepted_version=null (trovato {after.get('accepted_version')})"
            )
        if after.get("explicit_user_evidence") is not None:
            errs.append(
                f"{source_label}: status '{a_st}' richiede explicit_user_evidence=null"
            )

    return errs


# Campi semantici versionati di scaffold_state (tutti i campi operativi eccetto metadati di versione e timestamp)
VERSIONED_SCAFFOLD_FIELDS: List[str] = [
    "current_level",
    "previous_level",
    "transition",
    "transition_reason",
    "user_signal",
    "support_move",
    "grounded_facts",
    "pol03_delegation_requested",
    "stop_status",
]

# Vocabolario canonico segnali utente (scaffold_state.schema.json:72)
ALLOWED_CANONICAL_SCAFFOLD_SIGNALS: Set[str] = {
    "NO_ASSISTANCE_REQUESTED",
    "NO_PRIOR_SIGNAL",
    "ORIENTATION_REQUEST",
    "PERSISTENT_STALL",
    "MORE_HELP_REQUESTED",
    "AUTONOMY_RESUMED",
    "EXPLICIT_SELF_DIRECTION",
    "REFUSAL",
    "STOP_REQUEST",
    "CONTENT_QUESTION",
    "INSUFFICIENT_EVIDENCE",
}
ALLOWED_SCAFFOLD_SIGNALS = ALLOWED_CANONICAL_SCAFFOLD_SIGNALS

ALLOWED_SCAFFOLD_TRANSITIONS: Set[str] = {
    "HOLD",
    "ESCALATE",
    "FADE",
    "RESET_TO_AUTONOMY",
    "STOP",
    "NOT_APPLICABLE",
}

ALLOWED_SCAFFOLD_REASONS: Set[str] = {
    "INITIAL_MINIMUM_SUPPORT",
    "USER_REQUESTED_ORIENTATION",
    "USER_DECLARED_PERSISTENT_STALL",
    "USER_REQUESTED_MORE_STRUCTURE",
    "USER_FORMULATED_OWN_NEXT_STEP",
    "USER_REQUESTED_INDEPENDENCE",
    "USER_ALREADY_AUTONOMOUS_PLAN",
    "USER_REFUSED_SUPPORT",
    "CONTENT_REQUEST_OUT_OF_SCOPE",
    "MISSING_OBSERVABLE_SIGNAL",
    "NO_TRANSITION_INITIAL",
}

ALLOWED_SCAFFOLD_MOVES: Set[str] = {
    "ASK_ORIENTATION_QUESTION",
    "OFFER_DECLARED_OPTIONS",
    "OFFER_SELECTION_CRITERION",
    "DELEGATE_TO_POL03",
    "PROVIDE_PROCESS_STRUCTURE",
    "REQUEST_USER_DEFINED_ACTION",
    "AUTONOMY_INVITATION",
    "STOP_AND_ACKNOWLEDGE",
    "NONE",
}


def validate_scaffold_transition(
    before: Dict[str, Any],
    after: Dict[str, Any],
    turn: int,
    user_signal: Optional[str] = None,
    source_label: str = "scaffold_trajectory",
) -> List[str]:
    """
    Valida la transizione di scaffold_state da initial_state a final_state.
    Verifica algebra dei livelli (HOLD, ESCALATE, FADE, RESET_TO_AUTONOMY, STOP, NOT_APPLICABLE),
    incremento esatto (+1) di scaffold_version su QUALSIASI mutazione operativa dei 9 campi semantici,
    invarianza dei campi immutabili, corrispondenza di previous_level
    e compatibilita con user_signal canonico.
    """
    errs: List[str] = []

    # 1. Invarianza dei campi strutturali immutabili
    if after.get("created_at_turn") != before.get("created_at_turn"):
        errs.append(
            f"{source_label}: created_at_turn mutato da {before.get('created_at_turn')} a {after.get('created_at_turn')}"
        )

    # 2. Corrispondenza di previous_level con current_level precedente
    if after.get("previous_level") != before.get("current_level"):
        errs.append(
            f"{source_label}: previous_level finale ({after.get('previous_level')}) non coincide con current_level precedente ({before.get('current_level')})"
        )

    # 3. Monotonicita temporale
    b_up = before.get("updated_at_turn", 1)
    a_up = after.get("updated_at_turn", 1)
    if a_up > turn:
        errs.append(
            f"{source_label}: updated_at_turn ({a_up}) non puo essere futuro rispetto al turno corrente ({turn})"
        )
    if a_up < b_up:
        errs.append(f"{source_label}: updated_at_turn regredito da {b_up} a {a_up}")

    # 4. Rilevamento esaustivo del cambiamento operativo su tutti i 9 campi semantici
    b_facts = before.get("grounded_facts") or []
    a_facts = after.get("grounded_facts") or []
    facts_changed = sorted(b_facts) != sorted(a_facts)

    other_changed = any(
        before.get(f) != after.get(f)
        for f in VERSIONED_SCAFFOLD_FIELDS
        if f != "grounded_facts"
    )
    operational_changed = facts_changed or other_changed

    b_ver = before.get("scaffold_version", 1)
    a_ver = after.get("scaffold_version", 1)

    if a_ver < b_ver:
        errs.append(f"{source_label}: scaffold_version regredita da {b_ver} a {a_ver}")
    elif a_ver > b_ver + 1:
        errs.append(
            f"{source_label}: salto di scaffold_version non consentito da {b_ver} a {a_ver} (consentito solo +1 o invarianza)"
        )

    if not operational_changed:
        if a_ver != b_ver:
            errs.append(
                f"{source_label}: scaffold_version incrementata ({b_ver} -> {a_ver}) senza cambiamento operativo"
            )
        if a_up != b_up:
            errs.append(
                f"{source_label}: updated_at_turn ({a_up}) modificato rispetto a {b_up} senza cambiamento operativo"
            )
    else:
        if a_ver != b_ver + 1:
            errs.append(
                f"{source_label}: cambiamento operativo dello scaffold richiede incremento esatto di scaffold_version (+1, atteso {b_ver + 1}, trovato {a_ver})"
            )
        if a_up != turn:
            errs.append(
                f"{source_label}: cambiamento operativo dello scaffold richiede updated_at_turn == {turn} (trovato {a_up})"
            )

    # 5. Algebra dei livelli e verifica user_signal canonico
    lvl_b = before.get("current_level", 0)
    lvl_a = after.get("current_level", 0)
    trans = after.get("transition")
    stop_a = after.get("stop_status")

    # Verifica validita vocabolario user_signal se fornito
    if user_signal and user_signal != "NONE" and user_signal not in ALLOWED_CANONICAL_SCAFFOLD_SIGNALS:
        errs.append(f"{source_label}: user_signal non canonico: '{user_signal}'")

    if trans == "HOLD":
        if lvl_a != lvl_b:
            errs.append(f"{source_label}: transizione HOLD richiede current_level ({lvl_a}) == previous ({lvl_b})")

    elif trans == "ESCALATE":
        if lvl_a <= lvl_b:
            errs.append(f"{source_label}: transizione ESCALATE richiede current_level ({lvl_a}) > previous ({lvl_b})")
        if lvl_a > lvl_b + 1:
            errs.append(f"{source_label}: transizione ESCALATE non consente salti > 1 livello ({lvl_b} -> {lvl_a})")
        if lvl_b >= 3:
            errs.append(f"{source_label}: transizione ESCALATE non consentita da livello 3")
        allowed_escalate = {"PERSISTENT_STALL", "MORE_HELP_REQUESTED", "ORIENTATION_REQUEST", "INSUFFICIENT_EVIDENCE"}
        if user_signal and user_signal not in allowed_escalate:
            errs.append(f"{source_label}: transizione ESCALATE incompatibile con user_signal '{user_signal}'")

    elif trans == "FADE":
        if lvl_a >= lvl_b:
            errs.append(f"{source_label}: transizione FADE richiede current_level ({lvl_a}) < previous ({lvl_b})")
        if lvl_b == 0:
            errs.append(f"{source_label}: transizione FADE non consentita da livello 0")
        allowed_fade = {"AUTONOMY_RESUMED", "EXPLICIT_SELF_DIRECTION"}
        if user_signal and user_signal not in allowed_fade:
            errs.append(f"{source_label}: transizione FADE incompatibile con user_signal '{user_signal}'")

    elif trans == "RESET_TO_AUTONOMY":
        if lvl_a != 0:
            errs.append(f"{source_label}: transizione RESET_TO_AUTONOMY richiede current_level=0 (trovato {lvl_a})")
        allowed_reset = {"AUTONOMY_RESUMED", "EXPLICIT_SELF_DIRECTION"}
        if user_signal and user_signal not in allowed_reset:
            errs.append(f"{source_label}: transizione RESET_TO_AUTONOMY incompatibile con user_signal '{user_signal}'")

    elif trans == "STOP":
        if lvl_a != 0:
            errs.append(f"{source_label}: transizione STOP richiede current_level=0 (trovato {lvl_a})")
        if stop_a != "STOPPED":
            errs.append(f"{source_label}: transizione STOP richiede stop_status=STOPPED")
        allowed_stop = {"STOP_REQUEST", "REFUSAL"}
        if user_signal and user_signal not in allowed_stop:
            errs.append(f"{source_label}: transizione STOP incompatibile con user_signal '{user_signal}'")

    elif trans == "NOT_APPLICABLE":
        pass

    return errs
