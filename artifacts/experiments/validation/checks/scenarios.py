"""
Check 5, 6: Oracoli Level A, rubriche, metadati statici di traiettoria e record annidati.
Governato strettamente dai manifest espliciti con controlli di binding canonico a 4 vie,
integrita delle severity degli oracoli, e validazione rigorosa delle traiettorie scripted.
"""
import json
from ..context import ValidationContext
from ..report import ValidationReport
from ..manifests import (
    ORACLE_FILENAMES,
    CANONICAL_ORACLE_DIMENSIONS,
    CANONICAL_ORACLE_SEVERITIES,
    CANONICAL_CLAIMS,
    CANONICAL_SUPPORT_POLICIES,
    ALL_DEVELOPMENT_SCENARIOS,
)
from ..semantics.common import (
    validate_action_record_semantics,
    validate_scaffold_state_semantics,
)
from ..semantics.safety import validate_safety_state_semantics
from .fixtures import parse_and_validate_observable_syntax

EXPECTED_SCENARIO_POLICY_MAP = {
    "SCN-DEV-POL01-": ("POL-01_IF_THEN_PLAN", "ORACLE-POL01.v0.1.0.json"),
    "SCN-DEV-POL02-": ("POL-02_GRADED_SCAFFOLD", "ORACLE-POL02.v0.1.0.json"),
    "SCN-DEV-POL03-": ("POL-03_ACCEPTED_NEXT_ACTION", "ORACLE-POL03.v0.1.0.json"),
    "SCN-DEV-POL04-": ("POL-04_AUTONOMY_GATE", "ORACLE-POL04.v0.1.0.json"),
    "SCN-DEV-POL05-": ("POL-05_HUMAN_BOUNDARY", "ORACLE-POL05.v0.1.0.json"),
}


def check_oracles_and_rubrics(ctx: ValidationContext, report: ValidationReport) -> None:
    oracle_dir = ctx.paths.oracles
    policies_dir = ctx.paths.policies
    scenarios_dir = ctx.paths.development_scenarios

    # Closed-world check in oracle_dir
    if oracle_dir.exists():
        disk_oracles = {p.name for p in oracle_dir.glob("*.json")}
        expected_oracles = set(ORACLE_FILENAMES)
        unexpected = disk_oracles - expected_oracles
        for unexp in sorted(unexpected):
            report.error(f"File Oracle inatteso non registrato nel manifest: {unexp}")

    oracle_validator = ctx.get_validator("oracle.schema.json")
    if not oracle_validator:
        report.error("Validator per oracle.schema.json non disponibile nel contesto")

    # 5.1, 5.2, 5.3 Oracle checks against schema, canonical dimensions and canonical severities
    for of_name in ORACLE_FILENAMES:
        of = oracle_dir / of_name
        if not of.exists():
            report.error(f"File Oracle obbligatorio mancante: {of_name}")
            continue
        try:
            with open(of, "r", encoding="utf-8") as f:
                o_data = json.load(f)

            # Schema validation
            if oracle_validator:
                o_errors = list(oracle_validator.iter_errors(o_data))
                if o_errors:
                    for err in o_errors:
                        report.error(f"Oracle {of_name} non valido rispetto a oracle.schema.json: {err.message}")
                else:
                    report.ok(f"Oracle {of_name} conforme allo schema (Level A)")

            # Filename, ID and version consistency
            o_id = o_data.get("oracle_id")
            ver = o_data.get("version")
            if not o_id or not of_name.startswith(o_id):
                report.error(f"Oracle {of_name}: disallineamento tra nome file e oracle_id '{o_id}'")
            if ver != "0.1.0":
                report.error(f"Oracle {of_name}: version '{ver}' non canonica (atteso '0.1.0').")

            target_pol = o_data.get("target_policy")
            o_dims_list = [d["id"] for d in o_data.get("dimensions", []) if isinstance(d, dict) and "id" in d]
            o_dims = set(o_dims_list)
            if len(o_dims_list) != len(o_dims):
                report.error(f"Oracle {of_name}: contiene dimensioni duplicate nella lista dimensions")

            expected_dims = CANONICAL_ORACLE_DIMENSIONS.get(of_name, set())
            if o_dims != expected_dims:
                report.error(f"Dimensioni Oracle {of_name} disallineate. Trovate: {sorted(o_dims)}, Attese: {sorted(expected_dims)}")
            else:
                report.ok(f"Oracle {of_name} contiene le {len(expected_dims)} dimensioni canoniche: {sorted(expected_dims)}")

            # Check dimensions severities against canonical severities map
            expected_sevs = CANONICAL_ORACLE_SEVERITIES.get(of_name, {})
            for d in o_data.get("dimensions", []):
                dim_id = d.get("id")
                sev = d.get("severity")
                if not sev or sev not in ["CRITICAL", "MAJOR", "MINOR"]:
                    report.error(f"Oracle {of_name}: dimensione {dim_id} ha severity non valida '{sev}'")
                elif dim_id in expected_sevs:
                    exp_sev = expected_sevs[dim_id]
                    if sev != exp_sev:
                        report.error(
                            f"Oracle {of_name}: [SEVERITY DEGRADATION VETO] dimensione '{dim_id}' "
                            f"ha severity '{sev}' declassata rispetto alla canonica '{exp_sev}'."
                        )

            # Check matching policy contract
            if target_pol:
                pol_matching_file = policies_dir / f"{target_pol}.v0.1.0.json"
                if pol_matching_file.exists():
                    with open(pol_matching_file, "r", encoding="utf-8") as pf:
                        p_data = json.load(pf)
                    contract_oracle = p_data.get("oracle", {})
                    contract_dims = {d["id"] for d in contract_oracle.get("dimensions", []) if isinstance(d, dict) and "id" in d}
                    if contract_dims != expected_dims:
                        report.error(f"Dimensioni nel contratto {pol_matching_file.name} disallineate: {sorted(contract_dims)}")
                    else:
                        report.ok(f"Oracle nel contratto {pol_matching_file.name} allineato con le {len(expected_dims)} dimensioni canoniche")
                else:
                    report.error(f"Oracle {of_name}: target_policy '{target_pol}' non corrisponde ad alcun contratto in policies/")
            else:
                report.error(f"Oracle {of_name}: target_policy mancante")
        except Exception as e:
            report.error(f"Errore verifica oracle {of_name}: {e}")

    # 5.4 Controllo rigoroso del binding canonico a 4 vie
    for sc_name in ALL_DEVELOPMENT_SCENARIOS:
        sc_file = scenarios_dir / sc_name
        if not sc_file.exists():
            continue
        try:
            with open(sc_file, "r", encoding="utf-8") as f:
                s_data = json.load(f)

            sc_id = s_data.get("scenario_id")
            if not sc_id or not sc_name.startswith(sc_id):
                report.error(f"{sc_name}: scenario_id '{sc_id}' disallineato rispetto al nome file")

            eval_pol = s_data.get("evaluated_policy")
            oracle_ref = s_data.get("oracle_ref")
            if not oracle_ref:
                report.error(f"{sc_name}: campo obbligatorio 'oracle_ref' mancante nello scenario")
                continue

            if oracle_ref not in ORACLE_FILENAMES:
                report.error(f"{sc_name}: 'oracle_ref' fa riferimento a un oracle non registrato nel manifest: '{oracle_ref}'")
                continue

            # Binding canonico evaluated_policy ↔ oracle_ref
            for prefix, (exp_pol, exp_oracle) in EXPECTED_SCENARIO_POLICY_MAP.items():
                if sc_name.startswith(prefix):
                    if eval_pol != exp_pol:
                        report.error(f"{sc_name}: evaluated_policy '{eval_pol}' errato (atteso '{exp_pol}')")
                    if oracle_ref != exp_oracle:
                        report.error(f"{sc_name}: [CROSS-POLICY MISBINDING] oracle_ref '{oracle_ref}' errato per scenario {exp_pol} (atteso '{exp_oracle}')")

            expected_dims = CANONICAL_ORACLE_DIMENSIONS.get(oracle_ref, set())
            rubric = s_data.get("rubric")
            if not isinstance(rubric, list) or len(rubric) == 0:
                report.error(f"{sc_name}: rubrica mancante o vuota")
                continue

            rubric_dim_list = []
            has_duplicates = False
            seen_dims = set()
            for r_item in rubric:
                dim_id = r_item.get("dimension")
                if not dim_id:
                    report.error(f"{sc_name}: elemento della rubrica privo del campo 'dimension'")
                    continue
                if dim_id in seen_dims:
                    report.error(f"{sc_name}: dimensione duplicata nella rubrica: '{dim_id}'")
                    has_duplicates = True
                seen_dims.add(dim_id)
                rubric_dim_list.append(dim_id)

            rubric_dims_set = set(rubric_dim_list)

            missing_dims = expected_dims - rubric_dims_set
            unexpected_dims = rubric_dims_set - expected_dims

            if missing_dims:
                report.error(f"{sc_name}: rubrica incompleta rispetto a {oracle_ref}. Dimensioni mancanti: {sorted(missing_dims)}")
            if unexpected_dims:
                report.error(f"{sc_name}: rubrica contiene dimensioni inattese rispetto a {oracle_ref}: {sorted(unexpected_dims)}")

            if not missing_dims and not unexpected_dims and not has_duplicates:
                report.ok(f"{sc_name}: rubrica conforme a {oracle_ref} ({len(expected_dims)}/{len(expected_dims)} dimensioni canoniche)")
        except Exception as e:
            report.error(f"Errore controllo rubrica {sc_name}: {e}")


def check_scenarios_semantics(ctx: ValidationContext, report: ValidationReport) -> None:
    scenarios_dir = ctx.paths.development_scenarios

    for sc_name in ALL_DEVELOPMENT_SCENARIOS:
        sc_file = scenarios_dir / sc_name
        if not sc_file.exists():
            continue
        try:
            with open(sc_file, "r", encoding="utf-8") as f:
                s_data = json.load(f)

            # 1. Controllo split obbligatorio development
            split = s_data.get("split")
            if split != "development":
                report.error(f"{sc_name}: split non valido '{split}' (nella cartella development e ammesso solo split='development').")

            # 2. Controllo source_claims non vuoto e con claim censiti
            claims = s_data.get("source_claims", [])
            if not claims or len(claims) == 0:
                report.error(f"{sc_name}: source_claims non puo essere vuoto.")
            else:
                for c in claims:
                    if c not in CANONICAL_CLAIMS:
                        report.error(f"{sc_name}: claim inesistente o non censito nel ledger: '{c}'.")

            # 3. Controllo applicable_support_policies e forbidden_support_policies
            app_pols = s_data.get("applicable_support_policies", [])
            forb_pols = s_data.get("forbidden_support_policies", [])

            for ap in app_pols:
                if ap not in CANONICAL_SUPPORT_POLICIES:
                    report.error(f"{sc_name}: policy applicabile non censita '{ap}'.")

            for fp in forb_pols:
                if fp not in CANONICAL_SUPPORT_POLICIES:
                    report.error(f"{sc_name}: policy vietata non censita '{fp}'.")

            # Disgiunzione assoluta
            overlap = set(app_pols) & set(forb_pols)
            if overlap:
                report.error(f"{sc_name}: contraddizione nelle policy di supporto: {sorted(overlap)} e contemporaneamente applicabile e vietata.")

            # 4. Controllo expected_observables non vuoto e con sintassi/path DSL validi per la specifica policy valutata
            observables = s_data.get("expected_observables", [])
            sc_eval_policy = s_data.get("evaluated_policy")
            if not observables or len(observables) == 0:
                report.error(f"{sc_name}: expected_observables non puo essere vuoto.")
            else:
                for obs in observables:
                    is_valid_obs, err_msg_obs, _ = parse_and_validate_observable_syntax(obs, sc_eval_policy)
                    if not is_valid_obs:
                        report.error(
                            f"{sc_name}: [INVALID_OBSERVABLE_SYNTAX] observable '{obs}' non valido: {err_msg_obs}"
                        )

            # 5. Validazione approfondita di initial_state e turni
            try:
                from execution.scenario_expectations import validate_conditional_expectations
            except ImportError:
                from artifacts.experiments.execution.scenario_expectations import validate_conditional_expectations
            validate_conditional_expectations(s_data)
            init_st = s_data.get("initial_state", {})
            if not isinstance(init_st, dict):
                report.error(f"{sc_name}: initial_state deve essere un oggetto dizionario.")
                continue

            turn_idx = init_st.get("turn_index")
            max_turns = init_st.get("max_turns_allowed")
            term_status = init_st.get("terminal_status_expected")

            if not isinstance(turn_idx, int) or turn_idx < 1:
                report.error(f"{sc_name}: turn_index deve essere intero >= 1 (trovato: {turn_idx}).")
            if not isinstance(max_turns, int) or max_turns < 1:
                report.error(f"{sc_name}: max_turns_allowed deve essere intero >= 1 (trovato: {max_turns}).")
            if isinstance(turn_idx, int) and isinstance(max_turns, int):
                if turn_idx > max_turns:
                    report.error(f"{sc_name}: turn_index ({turn_idx}) supera max_turns_allowed ({max_turns}).")

            if not term_status or not isinstance(term_status, str) or len(term_status.strip()) < 3:
                report.error(f"{sc_name}: terminal_status_expected mancante o non valido: '{term_status}'.")

            # Invariante obbligatorio di stato in base alla policy valutata
            eval_pol = s_data.get("evaluated_policy")
            if eval_pol in ["POL-04_AUTONOMY_GATE", "POL-05_HUMAN_BOUNDARY"]:
                if "safety_state" not in init_st:
                    report.error(f"{sc_name}: scenario per {eval_pol} richiede obbligatoriamente safety_state in initial_state.")
                else:
                    errs = validate_safety_state_semantics(init_st["safety_state"], f"{sc_name} initial_state.safety_state")
                    for err in errs:
                        report.error(err)
                    if not errs:
                        report.ok(f"{sc_name}: safety_state iniziale valido e conforme")

            elif eval_pol == "POL-02_GRADED_SCAFFOLD":
                if "scaffold_state" in init_st:
                    errs = validate_scaffold_state_semantics(init_st["scaffold_state"], f"{sc_name} initial_state.scaffold_state")
                    for err in errs:
                        report.error(err)
                    if not errs:
                        report.ok(f"{sc_name}: scaffold_state iniziale valido e conforme")

            elif eval_pol == "POL-03_ACCEPTED_NEXT_ACTION":
                if "action_record" in init_st:
                    errs = validate_action_record_semantics(init_st["action_record"], f"{sc_name} initial_state.action_record")
                    for err in errs:
                        report.error(err)
                    if not errs:
                        report.ok(f"{sc_name}: action_record iniziale valido e conforme")

            # Cross-Policy Interactions & Nested checks
            if "nested_records" in s_data:
                for nr_name, nr_data in s_data["nested_records"].items():
                    if nr_name in ["pending_action", "pol03_accepted_action"] or "action" in nr_name:
                        errs = validate_action_record_semantics(nr_data, f"{sc_name} nested_records.{nr_name}")
                        for err in errs:
                            report.error(err)
                        if not errs:
                            report.ok(f"{sc_name}: action record annidato '{nr_name}' valido e conforme")
                    elif "scaffold" in nr_name or "level" in nr_name:
                        errs = validate_scaffold_state_semantics(nr_data, f"{sc_name} nested_records.{nr_name}")
                        for err in errs:
                            report.error(err)
                        if not errs:
                            report.ok(f"{sc_name}: scaffold state annidato valido e conforme")

            # 6. Validazione statica dei metadati di traiettoria; il runner non esegue il replay.
            traj = init_st.get("scripted_multi_turn_trajectory") or s_data.get("trajectory")
            if traj is not None:
                if not isinstance(traj, list) or len(traj) == 0:
                    report.error(f"{sc_name}: scripted_multi_turn_trajectory presente ma vuota.")
                else:
                    turns_seq = []
                    traj_errors = []
                    for idx, step in enumerate(traj):
                        if not isinstance(step, dict):
                            traj_errors.append(f"passo {idx} non e' un oggetto dizionario")
                            continue
                        t_num = step.get("turn") or step.get("turn_index")
                        u_msg = step.get("input") or step.get("user_message")
                        resp_type = step.get("expected_response_type")
                        exp_st = step.get("expected_state") or step.get("intermediate_state_expected")

                        if not isinstance(t_num, int) or t_num < 1:
                            traj_errors.append(f"passo {idx} ha numero turno non valido '{t_num}'")
                        else:
                            turns_seq.append(t_num)

                        if not u_msg or not isinstance(u_msg, str) or len(u_msg.strip()) == 0:
                            traj_errors.append(f"turno {t_num} ha messaggio input mancante o vuoto")
                        if not resp_type or not isinstance(resp_type, str):
                            traj_errors.append(f"turno {t_num} ha expected_response_type mancante")
                        if not exp_st or not isinstance(exp_st, str):
                            traj_errors.append(f"turno {t_num} ha expected_state mancante")

                    # Controllo sequenza turni: 1, 2, ..., N
                    expected_seq = list(range(1, len(traj) + 1))
                    if turns_seq != expected_seq:
                        traj_errors.append(f"sequenza turni disallineata: trovata {turns_seq}, attesa {expected_seq}")

                    if isinstance(max_turns, int) and len(traj) > max_turns:
                        traj_errors.append(f"lunghezza traiettoria ({len(traj)}) supera max_turns_allowed ({max_turns})")

                    # Controllo coerenza terminale
                    if traj and isinstance(traj[-1], dict):
                        final_state = traj[-1].get("expected_state") or traj[-1].get("intermediate_state_expected")
                        if final_state and term_status and final_state != term_status:
                            # Verifica compatibilita se non identico
                            if term_status not in final_state and final_state not in term_status:
                                traj_errors.append(
                                    f"stato finale della traiettoria '{final_state}' non coincide con terminal_status_expected '{term_status}'"
                                )

                    for terr in traj_errors:
                        report.error(f"{sc_name}: {terr}")

                    if not traj_errors:
                        report.ok(
                            f"{sc_name}: metadati statici di traiettoria validati "
                            f"({len(traj)} turni dichiarati; nessun replay eseguito -> {term_status})"
                        )
            else:
                report.ok(f"{sc_name}: scenario singolo turno verificato (turn {turn_idx}/{max_turns} -> {term_status})")
        except Exception as e:
            report.error(f"Errore semantica scenario {sc_name}: {e}")


# Alias per runner
check_scenario_semantics = check_scenarios_semantics
