"""
Check 4: Validazione e isolamento delle fixture POL-01..POL-05.
Verifica conformita a schema, vincoli semantici e collegamento profondo con gli scenari.
Include il machine-readable observable evaluator fail-closed con profili di policy rigorosi (per-policy whitelist).
"""
import json
import re
from pathlib import Path
from ..context import ValidationContext
from ..report import ValidationReport
from ..manifests import (
    POL01_VALID_FIXTURES,
    POL01_INVALID_FIXTURES,
    POL02_VALID_FIXTURES,
    POL02_INVALID_FIXTURES,
    POL03_VALID_FIXTURES,
    POL03_INVALID_FIXTURES,
    POL04_VALID_FIXTURES,
    POL04_INVALID_FIXTURES,
    POL05_VALID_FIXTURES,
    POL05_INVALID_FIXTURES,
    ALL_DEVELOPMENT_SCENARIOS,
)
from ..semantics.pol01 import validate_pol01_output_semantics
from ..semantics.pol02 import validate_pol02_output_semantics
from ..semantics.pol03 import validate_pol03_output_semantics
from ..semantics.safety import validate_safety_envelope_output_semantics

SCENARIO_TO_FILENAME = {fn.split("_")[0]: fn for fn in ALL_DEVELOPMENT_SCENARIOS}
VALID_SCENARIO_IDS = set(SCENARIO_TO_FILENAME.keys())

# Mappatura dei segnali governati da ciascuno scenario di sicurezza
SAFETY_SCENARIO_SIGNALS = {
    "SCN-DEV-POL04-01": {"STOP"},
    "SCN-DEV-POL04-02": {"REFUSAL"},
    "SCN-DEV-POL04-03": {"MODIFICATION"},
    "SCN-DEV-POL04-04": {"RESUME"},
    "SCN-DEV-POL04-05": {"REVOCATION"},
    "SCN-DEV-POL05-01": {"AMBIGUOUS_DISTRESS"},
    "SCN-DEV-POL05-02": {"HUMAN_IDENTITY_QUERY"},
    "SCN-DEV-POL05-03": {"CLINICAL_REQUEST"},
    "SCN-DEV-POL05-04": {"SUBSTITUTION_REQUEST"},
}

MISSING = object()

# Per-Policy Whitelist rigorosa per prevenire fail-open semantici tra domini di policy
POLICY_OBSERVABLE_PATHS = {
    "POL-01_IF_THEN_PLAN": {
        "policy_activated", "non_activation_reason", "clarification_requested",
        "cue_specificity", "cue_source", "action_observability", "action_source",
        "plan_formulated", "confirmation_status", "safety_violations", "missing_elements",
        "cue", "action", "plan", "turn_status",
    },
    "POL-02_GRADED_SCAFFOLD": {
        "policy_activated", "non_activation_reason", "level_before", "level_after",
        "transition", "transition_reason", "user_signal", "support_move",
        "orientation_question", "declared_options_used", "process_structure",
        "grounded_facts_used", "pol03_delegation_requested", "turn_status",
        "scaffold_state",
    },
    "POL-03_ACCEPTED_NEXT_ACTION": {
        "policy_activated", "non_activation_reason", "clarification_requested",
        "clarification_prompt", "proposed_action", "action_count", "action_observability",
        "grounded_facts_used", "action_record",
        "action_record.action_id", "action_record.action_version", "action_record.action_text",
        "action_record.action_source", "action_record.grounded_facts", "action_record.action_status",
        "action_record.accepted_version", "action_record.explicit_user_evidence",
        "pol01_handoff_ready", "user_evidence_recorded", "turn_status", "safety_violations",
    },
    "POL-04_AUTONOMY_GATE": {
        "scenario_id", "policy_id", "policy_version", "timestamp", "system_response",
        "decision_trace", "user_signal", "decision", "support_action_allowed",
        "proposed_support_action", "boundary_response", "revoked_target",
        "critical_violation", "violation_codes", "stop_status", "autonomy_status",
        "boundary_status", "resume_authorized", "safety_state_after", "safety_state_before",
        "turn_status",
    },
    "POL-05_HUMAN_BOUNDARY": {
        "scenario_id", "policy_id", "policy_version", "timestamp", "system_response",
        "decision_trace", "user_signal", "decision", "support_action_allowed",
        "proposed_support_action", "boundary_response", "revoked_target",
        "critical_violation", "violation_codes", "stop_status", "autonomy_status",
        "boundary_status", "resume_authorized", "safety_state_after", "safety_state_before",
        "turn_status",
    },
    "CROSS_POLICY_INTERACTION": {
        "policy_activated", "non_activation_reason", "clarification_requested",
        "cue_specificity", "cue_source", "action_observability", "action_source",
        "plan_formulated", "confirmation_status", "safety_violations", "missing_elements",
        "level_before", "level_after", "transition", "transition_reason", "user_signal",
        "support_move", "orientation_question", "declared_options_used", "process_structure",
        "grounded_facts_used", "pol03_delegation_requested", "clarification_prompt",
        "proposed_action", "action_count", "action_record",
        "action_record.action_id", "action_record.action_version", "action_record.action_text",
        "action_record.action_source", "action_record.grounded_facts", "action_record.action_status",
        "action_record.accepted_version", "action_record.explicit_user_evidence",
        "pol01_handoff_ready", "user_evidence_recorded", "turn_status",
        "pol01_activated", "pol02_level_after", "pol02_support_move", "pol02_transition",
        "pol03_action_status", "pol03_activated", "pol01_plan_status",
        "stop_status", "autonomy_status", "boundary_status", "resume_authorized", "decision",
    },
}

ALL_LEGAL_OBSERVABLE_PATHS = set.union(*POLICY_OBSERVABLE_PATHS.values())


def parse_and_validate_observable_syntax(expr: str, evaluated_policy: str = None) -> tuple[bool, str, dict]:
    """
    Esegue il parsing sintattico dell'observable e valida che il path appartenga al set consentito
    per la specifica policy valutata (o per l'unione canonica se evaluated_policy e' None).
    """
    expr_str = expr.strip()
    if not expr_str:
        return False, "Observable vuoto", {}

    if evaluated_policy:
        if evaluated_policy not in POLICY_OBSERVABLE_PATHS:
            return False, f"Policy non censita per la validazione degli observable: '{evaluated_policy}'", {}
        allowed_paths = POLICY_OBSERVABLE_PATHS[evaluated_policy]
    else:
        allowed_paths = ALL_LEGAL_OBSERVABLE_PATHS

    # Caso 1: var in [...]
    m_in = re.match(r"^([\w\.]+)\s+in\s+\[(.*)\]$", expr_str)
    if m_in:
        var_path = m_in.group(1)
        raw_items = m_in.group(2)
        if var_path not in allowed_paths:
            return False, f"Path '{var_path}' non consentito per la policy '{evaluated_policy}'", {}
        items = [i.strip().strip("'\"") for i in raw_items.split(",") if i.strip()]
        if not items:
            return False, f"Lista vuota per operatore 'in': '{expr_str}'", {}
        return True, "", {"path": var_path, "op": "in", "value": items}

    # Caso 2: var.length <= N
    m_len = re.match(r"^([\w\.]+)\.length\s*(<=|<|>=|>|==)\s*(\d+)$", expr_str)
    if m_len:
        var_path = m_len.group(1)
        op = m_len.group(2)
        target_len = int(m_len.group(3))
        if var_path not in allowed_paths:
            return False, f"Path '{var_path}' non consentito per la policy '{evaluated_policy}'", {}
        return True, "", {"path": var_path, "op": f".length {op}", "value": target_len}

    # Caso 3: var contains 'elem'
    m_cont = re.match(r"^([\w\.]+)\s+contains\s+['\"](.*)['\"]$", expr_str)
    if m_cont:
        var_path = m_cont.group(1)
        target_elem = m_cont.group(2)
        if var_path not in allowed_paths:
            return False, f"Path '{var_path}' non consentito per la policy '{evaluated_policy}'", {}
        return True, "", {"path": var_path, "op": "contains", "value": target_elem}

    # Caso 4: var == val / var != val
    m_eq = re.match(r"^([\w\.]+)\s*(==|!=)\s*(.*)$", expr_str)
    if m_eq:
        var_path = m_eq.group(1)
        op = m_eq.group(2)
        raw_target = m_eq.group(3).strip()

        if var_path not in allowed_paths:
            return False, f"Path '{var_path}' non consentito per la policy '{evaluated_policy}'", {}

        if raw_target == "null" or raw_target == "None":
            target = None
        elif raw_target.lower() == "true":
            target = True
        elif raw_target.lower() == "false":
            target = False
        elif raw_target == "[]":
            target = []
        elif (raw_target.startswith("'") and raw_target.endswith("'")) or (raw_target.startswith('"') and raw_target.endswith('"')):
            target = raw_target[1:-1]
        else:
            try:
                target = int(raw_target)
            except ValueError:
                target = raw_target

        return True, "", {"path": var_path, "op": op, "value": target}

    return False, f"Espressione observable non conforme alla grammatica DSL: '{expr_str}'", {}


def evaluate_observable_expression(expr: str, fixture_data: dict, evaluated_policy: str = None) -> tuple[bool, str]:
    """
    Valuta rigorosamente un observable contro la fixture con per-policy whitelist e gestione MISSING fail-closed.
    """
    is_valid_syntax, err_msg, parsed = parse_and_validate_observable_syntax(expr, evaluated_policy)
    if not is_valid_syntax:
        return False, f"[SYNTAX_ERROR] {err_msg}"

    env = dict(fixture_data)

    # POL-01 nested mappings
    if "cue" in fixture_data and isinstance(fixture_data["cue"], dict):
        env["cue_specificity"] = fixture_data["cue"].get("specificity")
        env["cue_source"] = fixture_data["cue"].get("source")
    elif "cue_specificity" not in env:
        env["cue_specificity"] = None
        env["cue_source"] = None

    if "action" in fixture_data and isinstance(fixture_data["action"], dict):
        env["action_observability"] = fixture_data["action"].get("observability")
        env["action_source"] = fixture_data["action"].get("source")
    elif "action_observability" not in env:
        env["action_observability"] = None
        env["action_source"] = None

    if "plan" in fixture_data and isinstance(fixture_data["plan"], dict):
        env["plan_formulated"] = fixture_data["plan"].get("formulated")
        env["confirmation_status"] = fixture_data["plan"].get("confirmation_status")
    elif "plan_formulated" not in env:
        env["plan_formulated"] = None
        env["confirmation_status"] = None

    if "violations" in fixture_data:
        env["safety_violations"] = fixture_data["violations"]

    if "clarification_requested" not in env:
        env["clarification_requested"] = False

    # POL-02 mappings
    if "scaffold_state_after" in fixture_data and isinstance(fixture_data["scaffold_state_after"], dict):
        env["level_after"] = fixture_data["scaffold_state_after"].get("level")
        env["pol02_level_after"] = fixture_data["scaffold_state_after"].get("level")
    elif "level_after" in fixture_data:
        env["pol02_level_after"] = fixture_data["level_after"]

    if "scaffold_state_before" in fixture_data and isinstance(fixture_data["scaffold_state_before"], dict):
        env["level_before"] = fixture_data["scaffold_state_before"].get("level")

    env["pol02_support_move"] = env.get("support_move")
    env["pol02_transition"] = env.get("transition")
    env["pol03_delegation_requested"] = env.get("pol03_delegation_requested", env.get("delegation_to_pol03_requested", False))

    # POL-03 mappings
    if isinstance(env.get("action_record"), dict):
        env["pol03_action_status"] = env["action_record"].get("action_status")

    # Safety state mappings
    if "safety_state_after" in fixture_data and isinstance(fixture_data["safety_state_after"], dict):
        ssa = fixture_data["safety_state_after"]
        env["stop_status"] = ssa.get("stop_status")
        env["autonomy_status"] = ssa.get("autonomy_status")
        env["boundary_status"] = ssa.get("boundary_status")
        env["resume_authorized"] = ssa.get("resume_authorized")

    def resolve_path(path: str):
        parts = path.strip().split(".")
        cur = env
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return MISSING
        return cur

    var_path = parsed["path"]
    val = resolve_path(var_path)

    # Fail-closed su path inesistente nell'ambiente
    if val is MISSING:
        return False, f"[MISSING_PATH] Il campo '{var_path}' non e' presente nell'output della fixture."

    op = parsed["op"]
    target = parsed["value"]

    if op == "in":
        ok = str(val) in target or val in target
        return ok, f"{var_path} ({repr(val)}) in {target}"

    if op.startswith(".length"):
        len_op = op.split()[1]
        if not isinstance(val, (list, tuple, dict, str)):
            return False, f"[TYPE_ERROR] Operazione .length non valida sul tipo {type(val)} per '{var_path}'"
        actual_len = len(val)
        if len_op == "<=": ok = actual_len <= target
        elif len_op == "<": ok = actual_len < target
        elif len_op == ">=": ok = actual_len >= target
        elif len_op == ">": ok = actual_len > target
        else: ok = actual_len == target
        return ok, f"{var_path}.length ({actual_len}) {len_op} {target}"

    if op == "contains":
        if not isinstance(val, (list, set, tuple)):
            return False, f"[TYPE_ERROR] Operazione contains richiede una lista/collezione, trovato {type(val)} per '{var_path}'"
        ok = target in val
        return ok, f"{var_path} ({repr(val)}) contains {repr(target)}"

    if op == "==":
        ok = (val == target)
        return ok, f"{var_path} ({repr(val)}) == {repr(target)}"

    if op == "!=":
        ok = (val != target)
        return ok, f"{var_path} ({repr(val)}) != {repr(target)}"

    return False, f"Operatore sconosciuto: {op}"


def _verify_fixture_linking(data: dict, f_name: str, expected_pol_id: str, ctx: ValidationContext, report: ValidationReport, is_valid_fixture: bool = True) -> None:
    """
    Verifica il collegamento profondo della fixture con lo scenario reale:
    - Esistenza fisica dello scenario su disco nel manifest
    - Corrispondenza di scenario_id, policy_version ('0.1.0') e policy_id
    - Compatibilita tra la policy valutata nello scenario e la fixture
    - Corrispondenza del segnale utente governato per policy di sicurezza (anti-cross-linking)
    - Conformita dello stato di attivazione rispetto ai vincoli dello scenario
    - Per le fixture valide: verifica machine-readable rigorosa di tutti gli expected_observables dello scenario per-policy.
    """
    sc_id = data.get("scenario_id")
    if not sc_id:
        report.error(f"Fixture {f_name}: campo 'scenario_id' mancante.")
        return

    base_id = sc_id.split("_")[0]
    if base_id not in VALID_SCENARIO_IDS and sc_id not in VALID_SCENARIO_IDS:
        report.error(f"Fixture {f_name}: scenario_id '{sc_id}' non esiste nel manifest degli scenari.")
        return

    pol_ver = data.get("policy_version")
    if pol_ver != "0.1.0":
        report.error(f"Fixture {f_name}: policy_version '{pol_ver}' non canonica (atteso '0.1.0').")

    pol_id = data.get("policy_id")
    if expected_pol_id and pol_id != expected_pol_id:
        report.error(f"Fixture {f_name}: policy_id '{pol_id}' disallineato rispetto alla policy attesa '{expected_pol_id}'.")

    # Caricamento e ispezione profonda dello scenario collegato
    sc_filename = SCENARIO_TO_FILENAME.get(base_id) or SCENARIO_TO_FILENAME.get(sc_id)
    if sc_filename:
        sc_path = ctx.paths.development_scenarios / sc_filename
        if sc_path.exists():
            try:
                with open(sc_path, "r", encoding="utf-8") as sf:
                    sc_data = json.load(sf)

                # Verifica compatibilita della policy valutata
                sc_eval_pol = sc_data.get("evaluated_policy")
                sc_app_pols = sc_data.get("applicable_support_policies", [])
                sc_forb_pols = sc_data.get("forbidden_support_policies", [])

                if sc_eval_pol != expected_pol_id and sc_eval_pol != "CROSS_POLICY_INTERACTION":
                    if expected_pol_id not in sc_app_pols and expected_pol_id not in sc_forb_pols:
                        report.error(
                            f"Fixture {f_name}: [CROSS-POLICY FIXTURE MISBINDING] scenario '{base_id}' "
                            f"valuta '{sc_eval_pol}' ma la fixture e' per '{expected_pol_id}'."
                        )

                # Verifica semantica per safety policies
                if expected_pol_id in ["POL-04_AUTONOMY_GATE", "POL-05_HUMAN_BOUNDARY"]:
                    user_sig = data.get("user_signal")
                    allowed_sigs = SAFETY_SCENARIO_SIGNALS.get(base_id)
                    # Per fixture valide il segnale deve coincidere strettamente; per fixture invalide ammettiamo segnali di test della violazione (es. NONE su unauthorized resume)
                    if is_valid_fixture and allowed_sigs and user_sig not in allowed_sigs:
                        report.error(
                            f"Fixture {f_name}: [CROSS-SCENARIO SIGNAL MISLINK] user_signal '{user_sig}' "
                            f"non corrisponde allo scenario '{base_id}' (governa: {sorted(allowed_sigs)})."
                        )

                # Verifica semantica per support policies
                else:
                    is_activated = data.get("policy_activated")
                    if is_valid_fixture and is_activated is True:
                        if expected_pol_id in sc_forb_pols:
                            report.error(
                                f"Fixture {f_name}: [POLICY CONFLICT] policy_activated=true ma la policy "
                                f"{expected_pol_id} e' dichiarata vietata nello scenario '{base_id}'."
                            )

                # Machine-readable observable verification per tutte le fixture valide
                if is_valid_fixture:
                    expected_observables = sc_data.get("expected_observables", [])
                    for obs in expected_observables:
                        ok, diag = evaluate_observable_expression(obs, data, sc_eval_pol)
                        if not ok:
                            report.error(
                                f"Fixture {f_name}: [SCENARIO OBSERVABLE MISMATCH] observable non soddisfatto "
                                f"per lo scenario '{base_id}': '{obs}' (Dettaglio: {diag})."
                            )

            except Exception as e:
                report.error(f"Fixture {f_name}: errore lettura scenario collegato '{sc_filename}': {e}")


def check_fixtures(ctx: ValidationContext, report: ValidationReport) -> None:
    # 4.1 Fixture POL-01
    pol01_fixtures_dir = ctx.paths.pol01_fixtures
    pol01_output_validator = ctx.get_validator("pol01_output.schema.json")
    if not pol01_output_validator:
        report.error("Impossibile validare fixture POL-01: validator pol01_output.schema.json non disponibile")
    else:
        if pol01_fixtures_dir.exists():
            disk_p1 = {p.name for p in pol01_fixtures_dir.glob("*.json")}
            expected_p1 = set(POL01_VALID_FIXTURES) | {f for f, _ in POL01_INVALID_FIXTURES}
            unexpected_p1 = disk_p1 - expected_p1
            for unexp in sorted(unexpected_p1):
                report.error(f"POL-01: file fixture inatteso non registrato nel manifest: {unexp}")

        for vf_name in POL01_VALID_FIXTURES:
            vf = pol01_fixtures_dir / vf_name
            if not vf.exists():
                report.error(f"POL-01: fixture valida obbligatoria mancante: {vf_name}")
                continue
            try:
                with open(vf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                schema_errors = list(pol01_output_validator.iter_errors(data))
                semantic_errors = validate_pol01_output_semantics(data, vf_name)
                if schema_errors or semantic_errors:
                    for err in schema_errors:
                        report.error(f"POL-01: fixture valida {vf_name} rifiutata dallo schema: {err.message}")
                    for err in semantic_errors:
                        report.error(f"POL-01: fixture valida {vf_name} errore semantico: {err}")
                else:
                    _verify_fixture_linking(data, vf_name, "POL-01_IF_THEN_PLAN", ctx, report, is_valid_fixture=True)
                    report.ok(f"POL-01: fixture valida {vf_name} conforme (schema + semantica + observables)")
            except Exception as e:
                report.error(f"POL-01: errore lettura fixture {vf_name}: {e}")

        for inv_name, expected_reason in POL01_INVALID_FIXTURES:
            inv_file = pol01_fixtures_dir / inv_name
            if not inv_file.exists():
                report.error(f"POL-01: fixture invalida obbligatoria mancante: {inv_name}")
                continue
            try:
                with open(inv_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                schema_errors = list(pol01_output_validator.iter_errors(data))
                semantic_errors = validate_pol01_output_semantics(data, inv_name)
                if not schema_errors and not semantic_errors:
                    report.error(f"POL-01: fixture invalida {inv_name} accettata erroneamente (atteso fallimento: {expected_reason})")
                else:
                    _verify_fixture_linking(data, inv_name, "POL-01_IF_THEN_PLAN", ctx, report, is_valid_fixture=False)
                    report.ok(f"POL-01: fixture invalida {inv_name} correttamente respinta ({expected_reason})")
            except Exception as e:
                report.error(f"POL-01: errore lettura fixture invalida {inv_name}: {e}")

    # 4.2 Fixture POL-02
    pol02_fixtures_dir = ctx.paths.pol02_fixtures
    pol02_output_validator = ctx.get_validator("pol02_output.schema.json")
    if not pol02_output_validator:
        report.error("Impossibile validare fixture POL-02: validator pol02_output.schema.json non disponibile")
    else:
        if pol02_fixtures_dir.exists():
            disk_p2 = {p.name for p in pol02_fixtures_dir.glob("*.json")}
            expected_p2 = set(POL02_VALID_FIXTURES) | {f for f, _ in POL02_INVALID_FIXTURES}
            unexpected_p2 = disk_p2 - expected_p2
            for unexp in sorted(unexpected_p2):
                report.error(f"POL-02: file fixture inatteso non registrato nel manifest: {unexp}")

        for vf_name in POL02_VALID_FIXTURES:
            vf = pol02_fixtures_dir / vf_name
            if not vf.exists():
                report.error(f"POL-02: fixture valida obbligatoria mancante: {vf_name}")
                continue
            try:
                with open(vf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                schema_errors = list(pol02_output_validator.iter_errors(data))
                sem_errors = validate_pol02_output_semantics(data, f"POL-02 {vf_name}")
                if schema_errors or sem_errors:
                    for err in schema_errors:
                        report.error(f"POL-02: fixture valida {vf_name} errore schema: {err.message}")
                    for err in sem_errors:
                        report.error(f"POL-02: fixture valida {vf_name} errore semantico: {err}")
                else:
                    _verify_fixture_linking(data, vf_name, "POL-02_GRADED_SCAFFOLD", ctx, report, is_valid_fixture=True)
                    report.ok(f"POL-02: fixture valida {vf_name} conforme (schema + semantica + observables)")
            except Exception as e:
                report.error(f"POL-02: errore lettura fixture {vf_name}: {e}")

        for inv_name, expected_reason in POL02_INVALID_FIXTURES:
            inv_file = pol02_fixtures_dir / inv_name
            if not inv_file.exists():
                report.error(f"POL-02: fixture invalida obbligatoria mancante: {inv_name}")
                continue
            try:
                with open(inv_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                schema_errors = list(pol02_output_validator.iter_errors(data))
                sem_errors = validate_pol02_output_semantics(data, f"POL-02 {inv_name}")
                if not schema_errors and not sem_errors:
                    report.error(f"POL-02: fixture invalida {inv_name} accettata erroneamente (atteso fallimento: {expected_reason})")
                else:
                    _verify_fixture_linking(data, inv_name, "POL-02_GRADED_SCAFFOLD", ctx, report, is_valid_fixture=False)
                    report.ok(f"POL-02: fixture invalida {inv_name} correttamente respinta ({expected_reason})")
            except Exception as e:
                report.error(f"POL-02: errore lettura fixture invalida {inv_name}: {e}")

    # 4.3 Fixture POL-03
    pol03_fixtures_dir = ctx.paths.pol03_fixtures
    pol03_output_validator = ctx.get_validator("pol03_output.schema.json")
    if not pol03_output_validator:
        report.error("Impossibile validare fixture POL-03: validator pol03_output.schema.json non disponibile")
    else:
        if pol03_fixtures_dir.exists():
            disk_p3 = {p.name for p in pol03_fixtures_dir.glob("*.json")}
            expected_p3 = set(POL03_VALID_FIXTURES) | {f for f, _ in POL03_INVALID_FIXTURES}
            unexpected_p3 = disk_p3 - expected_p3
            for unexp in sorted(unexpected_p3):
                report.error(f"POL-03: file fixture inatteso non registrato nel manifest: {unexp}")

        for vf_name in POL03_VALID_FIXTURES:
            vf = pol03_fixtures_dir / vf_name
            if not vf.exists():
                report.error(f"POL-03: fixture valida obbligatoria mancante: {vf_name}")
                continue
            try:
                with open(vf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                schema_errors = list(pol03_output_validator.iter_errors(data))
                sem_errors = validate_pol03_output_semantics(data, f"POL-03 {vf_name}")
                if schema_errors or sem_errors:
                    for err in schema_errors:
                        report.error(f"POL-03: fixture valida {vf_name} errore schema: {err.message}")
                    for err in sem_errors:
                        report.error(f"POL-03: fixture valida {vf_name} errore semantico: {err}")
                else:
                    _verify_fixture_linking(data, vf_name, "POL-03_ACCEPTED_NEXT_ACTION", ctx, report, is_valid_fixture=True)
                    report.ok(f"POL-03: fixture valida {vf_name} conforme (schema + semantica + observables)")
            except Exception as e:
                report.error(f"POL-03: errore lettura fixture {vf_name}: {e}")

        for inv_name, expected_reason in POL03_INVALID_FIXTURES:
            inv_file = pol03_fixtures_dir / inv_name
            if not inv_file.exists():
                report.error(f"POL-03: fixture invalida obbligatoria mancante: {inv_name}")
                continue
            try:
                with open(inv_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                schema_errors = list(pol03_output_validator.iter_errors(data))
                sem_errors = validate_pol03_output_semantics(data, f"POL-03 {inv_name}")
                if not schema_errors and not sem_errors:
                    report.error(f"POL-03: fixture invalida {inv_name} accettata erroneamente (atteso fallimento: {expected_reason})")
                else:
                    _verify_fixture_linking(data, inv_name, "POL-03_ACCEPTED_NEXT_ACTION", ctx, report, is_valid_fixture=False)
                    report.ok(f"POL-03: fixture invalida {inv_name} correttamente respinta ({expected_reason})")
            except Exception as e:
                report.error(f"POL-03: errore lettura fixture invalida {inv_name}: {e}")

    # 4.4 Fixture POL-04
    pol04_fixtures_dir = ctx.paths.pol04_fixtures
    safety_output_validator = ctx.get_validator("safety_envelope_output.schema.json")
    if not safety_output_validator:
        report.error("Impossibile validare fixture POL-04: validator safety_envelope_output.schema.json non disponibile")
    else:
        if pol04_fixtures_dir.exists():
            disk_p4 = {p.name for p in pol04_fixtures_dir.glob("*.json")}
            expected_p4 = set(POL04_VALID_FIXTURES) | {f for f, _ in POL04_INVALID_FIXTURES}
            unexpected_p4 = disk_p4 - expected_p4
            for unexp in sorted(unexpected_p4):
                report.error(f"POL-04: file fixture inatteso non registrato nel manifest: {unexp}")

        for vf_name in POL04_VALID_FIXTURES:
            vf = pol04_fixtures_dir / vf_name
            if not vf.exists():
                report.error(f"POL-04: fixture valida obbligatoria mancante: {vf_name}")
                continue
            try:
                with open(vf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                schema_errors = list(safety_output_validator.iter_errors(data))
                sem_errors = validate_safety_envelope_output_semantics(data, f"POL-04 {vf_name}")
                if schema_errors or sem_errors:
                    for err in schema_errors:
                        report.error(f"POL-04: fixture valida {vf_name} errore schema: {err.message}")
                    for err in sem_errors:
                        report.error(f"POL-04: fixture valida {vf_name} errore semantico: {err}")
                else:
                    _verify_fixture_linking(data, vf_name, "POL-04_AUTONOMY_GATE", ctx, report, is_valid_fixture=True)
                    report.ok(f"POL-04: fixture valida {vf_name} conforme (schema + semantica + observables)")
            except Exception as e:
                report.error(f"POL-04: errore lettura fixture {vf_name}: {e}")

        for inv_name, expected_reason in POL04_INVALID_FIXTURES:
            inv_file = pol04_fixtures_dir / inv_name
            if not inv_file.exists():
                report.error(f"POL-04: fixture invalida obbligatoria mancante: {inv_name}")
                continue
            try:
                with open(inv_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                schema_errors = list(safety_output_validator.iter_errors(data))
                sem_errors = validate_safety_envelope_output_semantics(data, f"POL-04 {inv_name}")
                if not schema_errors and not sem_errors:
                    report.error(f"POL-04: fixture invalida {inv_name} accettata erroneamente (atteso fallimento: {expected_reason})")
                else:
                    _verify_fixture_linking(data, inv_name, "POL-04_AUTONOMY_GATE", ctx, report, is_valid_fixture=False)
                    report.ok(f"POL-04: fixture invalida {inv_name} correttamente respinta ({expected_reason})")
            except Exception as e:
                report.error(f"POL-04: errore lettura fixture invalida {inv_name}: {e}")

    # 4.5 Fixture POL-05
    pol05_fixtures_dir = ctx.paths.pol05_fixtures
    if not safety_output_validator:
        report.error("Impossibile validare fixture POL-05: validator safety_envelope_output.schema.json non disponibile")
    else:
        if pol05_fixtures_dir.exists():
            disk_p5 = {p.name for p in pol05_fixtures_dir.glob("*.json")}
            expected_p5 = set(POL05_VALID_FIXTURES) | {f for f, _ in POL05_INVALID_FIXTURES}
            unexpected_p5 = disk_p5 - expected_p5
            for unexp in sorted(unexpected_p5):
                report.error(f"POL-05: file fixture inatteso non registrato nel manifest: {unexp}")

        for vf_name in POL05_VALID_FIXTURES:
            vf = pol05_fixtures_dir / vf_name
            if not vf.exists():
                report.error(f"POL-05: fixture valida obbligatoria mancante: {vf_name}")
                continue
            try:
                with open(vf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                schema_errors = list(safety_output_validator.iter_errors(data))
                sem_errors = validate_safety_envelope_output_semantics(data, f"POL-05 {vf_name}")
                if schema_errors or sem_errors:
                    for err in schema_errors:
                        report.error(f"POL-05: fixture valida {vf_name} errore schema: {err.message}")
                    for err in sem_errors:
                        report.error(f"POL-05: fixture valida {vf_name} errore semantico: {err}")
                else:
                    _verify_fixture_linking(data, vf_name, "POL-05_HUMAN_BOUNDARY", ctx, report, is_valid_fixture=True)
                    report.ok(f"POL-05: fixture valida {vf_name} conforme (schema + semantica + observables)")
            except Exception as e:
                report.error(f"POL-05: errore lettura fixture {vf_name}: {e}")

        for inv_name, expected_reason in POL05_INVALID_FIXTURES:
            inv_file = pol05_fixtures_dir / inv_name
            if not inv_file.exists():
                report.error(f"POL-05: fixture invalida obbligatoria mancante: {inv_name}")
                continue
            try:
                with open(inv_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                schema_errors = list(safety_output_validator.iter_errors(data))
                sem_errors = validate_safety_envelope_output_semantics(data, f"POL-05 {inv_name}")
                if not schema_errors and not sem_errors:
                    report.error(f"POL-05: fixture invalida {inv_name} accettata erroneamente (atteso fallimento: {expected_reason})")
                else:
                    _verify_fixture_linking(data, inv_name, "POL-05_HUMAN_BOUNDARY", ctx, report, is_valid_fixture=False)
                    report.ok(f"POL-05: fixture invalida {inv_name} correttamente respinta ({expected_reason})")
            except Exception as e:
                report.error(f"POL-05: errore lettura fixture invalida {inv_name}: {e}")
