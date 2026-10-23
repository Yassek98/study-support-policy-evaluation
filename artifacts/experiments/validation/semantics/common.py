"""
Funzioni pure di validazione semantica comune (RFC 3339, action_record, scaffold_state).
"""
import re
from datetime import datetime
from typing import Any

RFC3339_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def is_valid_iso8601(ts_str: Any) -> bool:
    if not isinstance(ts_str, str):
        return False
    if not RFC3339_REGEX.match(ts_str):
        return False
    try:
        ts_norm = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_norm)
        if dt.tzinfo is None:
            return False
        return True
    except Exception:
        return False


def validate_action_record_semantics(record: dict[str, Any], source_label: str = "action_record") -> list[str]:
    errs = []
    v = record.get("action_version", 1)
    av = record.get("accepted_version")
    st = record.get("action_status")
    ev = record.get("explicit_user_evidence")
    src = record.get("action_source")
    facts = record.get("grounded_facts", [])
    cat = record.get("created_at_turn", 1)
    uat = record.get("updated_at_turn", 1)
    if uat < cat:
        errs.append(f"{source_label}: updated_at_turn ({uat}) < created_at_turn ({cat})")

    if st == "ACTION_ACCEPTED":
        if av is None or ev is None:
            errs.append(f"{source_label}: ACTION_ACCEPTED richiede accepted_version e explicit_user_evidence non null")
        elif av != v:
            errs.append(f"{source_label}: accepted_version ({av}) deve essere esattamente uguale ad action_version ({v})")
    else:
        if av is not None:
            errs.append(f"{source_label}: stato {st} richiede accepted_version=null (trovato {av})")
        if ev is not None:
            errs.append(f"{source_label}: stato {st} richiede explicit_user_evidence=null (trovato {ev})")

    if src == "SYSTEM_PROPOSED_FROM_DECLARED_FACTS":
        if not facts or len(facts) == 0:
            errs.append(f"{source_label}: SYSTEM_PROPOSED_FROM_DECLARED_FACTS richiede grounded_facts non vuoto")

    return errs


def validate_scaffold_state_semantics(state: dict[str, Any], source_label: str = "scaffold_state") -> list[str]:
    errs = []
    cat = state.get("created_at_turn", 1)
    uat = state.get("updated_at_turn", 1)
    if uat < cat:
        errs.append(f"{source_label}: updated_at_turn ({uat}) < created_at_turn ({cat})")

    lvl = state.get("current_level")
    stop_st = state.get("stop_status")
    del_req = state.get("pol03_delegation_requested")

    if stop_st == "STOPPED":
        if lvl != 0:
            errs.append(f"{source_label}: stop_status=STOPPED richiede current_level=0 (trovato {lvl})")
        if del_req is not False:
            errs.append(f"{source_label}: stop_status=STOPPED richiede pol03_delegation_requested=false")

    if del_req is True:
        if lvl != 3:
            errs.append(f"{source_label}: pol03_delegation_requested=true richiede current_level=3 (trovato {lvl})")
        if stop_st != "ACTIVE":
            errs.append(f"{source_label}: pol03_delegation_requested=true richiede stop_status=ACTIVE")

    return errs
