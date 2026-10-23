"""
Check 13: Validazione formale delle configurazioni sperimentali ($2^3$),
dei moduli di prompt e della composizione deterministica.
"""

import json
import re
from pathlib import Path
from jsonschema import Draft202012Validator

from ..context import ValidationContext
from ..report import ValidationReport
from ..manifests import (
    ALL_CONFIGURATION_IDS,
    ALL_CONFIGURATION_FILENAMES,
    ALL_PROMPT_MODULE_FILENAMES,
    CANONICAL_FACTORIAL_MATRIX,
    EXPECTED_EXECUTION_FIXTURES,
)
try:
    from execution.configuration_loader import load_validated_configuration
    from execution.prompt_composer import compose_prompt
    from execution.run_record_validator import validate_run_record_semantics
    from execution.structured_output import (
        bundle_schema_with_defs,
        project_model_generated_schema,
        specialize_agent_turn_output_schema,
    )
except ImportError:
    from artifacts.experiments.execution.configuration_loader import load_validated_configuration
    from artifacts.experiments.execution.prompt_composer import compose_prompt
    from artifacts.experiments.execution.run_record_validator import validate_run_record_semantics
    from artifacts.experiments.execution.structured_output import (
        bundle_schema_with_defs,
        project_model_generated_schema,
        specialize_agent_turn_output_schema,
    )


def check_configurations(ctx: ValidationContext, report: ValidationReport) -> None:
    configs_dir = ctx.paths.configurations
    base_dir = ctx.paths.base

    matrix_validator = ctx.get_validator("configuration_matrix.schema.json")
    config_validator = ctx.get_validator("configuration.schema.json")
    run_record_validator = ctx.get_validator("run_record.schema.json")
    agent_turn_validator = ctx.get_validator("agent_turn_output.schema.json")

    if not config_validator:
        report.error("Check 13: Impossibile validare le configurazioni: validator per configuration.schema.json non disponibile")
        return

    # 1. Validazione della Matrice Fattoriale Canonica 2^3
    matrix_path = configs_dir / "CONFIGURATION_MATRIX.v0.1.0.json"
    if not matrix_path.exists():
        report.error(f"Check 13: File matrice canonica mancante: {matrix_path.name}")
        return

    try:
        with open(matrix_path, "r", encoding="utf-8") as f:
            matrix_data = json.load(f)

        if matrix_validator:
            m_errors = list(matrix_validator.iter_errors(matrix_data))
            if m_errors:
                for err in m_errors:
                    report.error(f"Check 13: Matrice non conforme a schema: {err.message}")
            else:
                report.ok("Check 13: CONFIGURATION_MATRIX.v0.1.0.json conforme a configuration_matrix.schema.json")

        if matrix_data.get("matrix_id") != "FACTORIAL_2X3_V0_1_0":
            report.error(f"Check 13: matrix_id non canonico: {matrix_data.get('matrix_id')}")
        if matrix_data.get("status") != "CANDIDATE":
            report.error(f"Check 13: status matrice non CANDIDATE: {matrix_data.get('status')}")
        if matrix_data.get("architecture") != "SINGLE_AGENT":
            report.error(f"Check 13: architettura matrice non SINGLE_AGENT: {matrix_data.get('architecture')}")

        safety_env = matrix_data.get("safety_envelope", [])
        expected_safety = ["POL-04_AUTONOMY_GATE", "POL-05_HUMAN_BOUNDARY"]
        if safety_env != expected_safety:
            report.error(f"Check 13: safety_envelope della matrice discordante da specifiche: {safety_env}")

        matrix_configs = matrix_data.get("configurations", [])
        if len(matrix_configs) != 8:
            report.error(f"Check 13: Matrice contiene {len(matrix_configs)} configurazioni anziche le 8 canoniche")

        matrix_ids = [c.get("configuration_id") for c in matrix_configs]
        if set(matrix_ids) != set(ALL_CONFIGURATION_IDS):
            report.error(f"Check 13: ID configurazioni nella matrice discordanti: atteso {ALL_CONFIGURATION_IDS}, trovato {matrix_ids}")

        for entry in matrix_configs:
            c_id = entry.get("configuration_id")
            c_factors = entry.get("factors", {})
            p1 = c_factors.get("P1_POL01")
            p2 = c_factors.get("P2_POL02")
            p3 = c_factors.get("P3_POL03")
            if c_id in CANONICAL_FACTORIAL_MATRIX:
                expected_f = CANONICAL_FACTORIAL_MATRIX[c_id]
                if (p1, p2, p3) != expected_f:
                    report.error(f"Check 13: Fattori discordanti per {c_id}: atteso {expected_f}, trovato {(p1, p2, p3)}")

            m_ref = entry.get("manifest_ref")
            if not m_ref:
                report.error(f"Check 13: manifest_ref mancante per {c_id}")
            else:
                expected_m_ref = f"configurations/{c_id}.v0.1.0.json"
                if m_ref != expected_m_ref:
                    report.error(f"Check 13: Incoerenza manifest_ref per '{c_id}': atteso {expected_m_ref}, trovato {m_ref}")
                ref_file = base_dir / m_ref
                if not ref_file.exists():
                    report.error(f"Check 13: File referenziato dalla matrice non trovato su disco: {m_ref}")

        report.ok("Matrice canonica 2^3 formalizzata e verificata (8 combinazioni biiettive)")

    except Exception as e:
        report.error(f"Check 13: Errore durante la validazione della matrice: {e}")

    # 2. Controllo closed-world sui file in configurations/
    if configs_dir.exists():
        disk_files = {p.name for p in configs_dir.glob("*.json")}
        expected_files = set(ALL_CONFIGURATION_FILENAMES) | {
            "CONFIGURATION_MATRIX.v0.1.0.json",
            "DEFAULT_INITIAL_STATES.v0.1.0.json",
        }
        unexpected = disk_files - expected_files
        for unexp in sorted(unexpected):
            report.error(f"Check 13: File inatteso non registrato in configurations/: {unexp}")

    # 3. Controllo closed-world sui moduli di prompt in prompts/modules/
    prompt_mods_dir = base_dir / "prompts" / "modules"
    if prompt_mods_dir.exists():
        disk_modules = {p.name for p in prompt_mods_dir.glob("*.md")}
        expected_modules = set(ALL_PROMPT_MODULE_FILENAMES)
        unexpected_mods = disk_modules - expected_modules
        for unexp in sorted(unexpected_mods):
            report.error(f"Check 13: Modulo di prompt inatteso non registrato in prompts/modules/: {unexp}")

    for mod_filename in ALL_PROMPT_MODULE_FILENAMES:
        mod_path = prompt_mods_dir / mod_filename
        if not mod_path.exists():
            report.error(f"Check 13: Modulo di prompt canonico mancante: {mod_filename}")

    # 4. Validazione formale di ciascun manifest di configurazione tramite configuration_loader
    for cfg_filename in ALL_CONFIGURATION_FILENAMES:
        cfg_path = configs_dir / cfg_filename
        if not cfg_path.exists():
            report.error(f"Check 13: Manifest di configurazione mancante: {cfg_filename}")
            continue

        cfg_errors_before = len(report.errors)
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw_cfg_data = json.load(f)

            cfg_id = raw_cfg_data.get("configuration_id")
            validated_cfg = load_validated_configuration(raw_cfg_data, repo_root=base_dir)

            composed1 = compose_prompt(validated_cfg, repo_root=base_dir)
            composed2 = compose_prompt(validated_cfg, repo_root=base_dir)

            if not composed1.prompt_sha256 or len(composed1.prompt_sha256) != 64:
                report.error(f"Check 13: Manifest {cfg_filename} prompt_sha256 non valido")
            if not composed1.artifact_fingerprint or len(composed1.artifact_fingerprint) != 64:
                report.error(f"Check 13: Manifest {cfg_filename} artifact_fingerprint non valido")
            if composed1.prompt_sha256 != composed2.prompt_sha256:
                report.error(f"Check 13: Manifest {cfg_filename} prompt non deterministico: hash differenti")
            if composed1.artifact_fingerprint != composed2.artifact_fingerprint:
                report.error(f"Check 13: Manifest {cfg_filename} artifact fingerprint non deterministico: hash differenti")
            if composed1.configuration_id != cfg_id:
                report.error(f"Check 13: ComposedPrompt id discordante: {composed1.configuration_id} vs {cfg_id}")

            if len(report.errors) == cfg_errors_before:
                report.ok(f"Configurazione {cfg_id}: manifest valido, binding biiettivi verificati e prompt deterministico")

        except Exception as e:
            report.error(f"Check 13: Errore validazione manifest {cfg_filename}: {e}")

    # 5. Validazione Formale delle Fixture di Esecuzione tramite inventario chiuso autoritativo
    exec_fixtures_dir = base_dir / "fixtures" / "execution"
    if not exec_fixtures_dir.is_dir():
        report.error(f"Check 13: Directory fixture di esecuzione assente: {exec_fixtures_dir}")
    else:
        observed_files = set()
        for p in exec_fixtures_dir.iterdir():
            if not p.is_file() or not p.name.endswith(".json"):
                report.error(f"Check 13: File non JSON o non classificabile in fixtures/execution/: {p.name}")
            else:
                observed_files.add(p.name)

        expected_files = set(EXPECTED_EXECUTION_FIXTURES.keys())
        missing_fixtures = expected_files - observed_files
        extra_fixtures = observed_files - expected_files

        if missing_fixtures:
            for mf in sorted(missing_fixtures):
                report.error(f"Check 13: Fixture di esecuzione prevista mancante: {mf}")

        if extra_fixtures:
            for ef in sorted(extra_fixtures):
                report.error(f"Check 13: Fixture di esecuzione inattesa trovata nell'inventario: {ef}")

        for fname, fix_meta in EXPECTED_EXECUTION_FIXTURES.items():
            fix_path = exec_fixtures_dir / fname
            if not fix_path.is_file():
                continue
            try:
                with open(fix_path, "r", encoding="utf-8") as ff:
                    fix_data = json.load(ff)

                f_type = fix_meta["type"]
                exp_valid = fix_meta["expected_valid"]

                if f_type == "AGENT_TURN_OUTPUT":
                    val = agent_turn_validator
                    errors = list(val.iter_errors(fix_data))
                    if exp_valid:
                        if errors:
                            for err in errors:
                                report.error(f"Check 13: Fixture valida '{fname}' non conforme a agent_turn_output.schema.json: {err.message}")
                        else:
                            report.ok(f"Fixture di esecuzione valida '{fname}' conforme a agent_turn_output.schema.json")
                    else:
                        if not errors:
                            report.error(f"Check 13: Fixture negativa '{fname}' inaspettatamente valida per agent_turn_output.schema.json")
                        else:
                            report.ok(f"Fixture negativa '{fname}' correttamente respinta da agent_turn_output.schema.json")

                elif f_type == "RUN_RECORD":
                    cfg_id = fix_data.get("configuration", {}).get("configuration_id", "B0_SIMPLE")
                    val_cfg = load_validated_configuration(f"configurations/{cfg_id}.v0.1.0.json", repo_root=base_dir)

                    if exp_valid:
                        validate_run_record_semantics(fix_data, val_cfg, repo_root=base_dir)
                        report.ok(f"Fixture di esecuzione valida '{fname}' conforme a run_record e validata semanticamente")
                    else:
                        exp_err = fix_meta.get("expected_error", "")
                        raised_msg = ""
                        try:
                            val = run_record_validator
                            schema_errors = list(val.iter_errors(fix_data))
                            if schema_errors:
                                raised_msg = f"schema: {schema_errors[0].message}"
                            else:
                                validate_run_record_semantics(fix_data, val_cfg, repo_root=base_dir)
                        except Exception as ex:
                            raised_msg = str(ex)

                        if not raised_msg:
                            report.error(f"Check 13: Fixture negativa '{fname}' inaspettatamente accettata!")
                        else:
                            if exp_err and not re.search(re.escape(exp_err), raised_msg):
                                report.error(
                                    f"Check 13: Fixture negativa '{fname}' fallita per motivo inatteso: atteso '{exp_err}', ottenuto '{raised_msg}'"
                                )
                            else:
                                report.ok(f"Fixture di esecuzione negativa '{fname}' correttamente respinta per il motivo registrato")
            except Exception as fe:
                report.error(f"Check 13: Errore durante elaborazione fixture '{fname}': {fe}")

    # 6. Controllo Automatico e Approfondito di Coerenza Lessicale e Valori Prompt-Schema
    lexical_errors_before = len(report.errors)

    # 6.1 Struttura e proprietà
    prompt_schema_checks = [
        (
            "POL-01_IF_THEN_PLAN.v0.1.0.md",
            "pol01_output.schema.json",
            ["action.source", "plan.confirmation_status", "action", "plan"]
        ),
        (
            "POL-02_GRADED_SCAFFOLD.v0.1.0.md",
            "pol02_output.schema.json",
            ["pol03_delegation_requested", "level_after"]
        ),
        (
            "POL-03_ACCEPTED_NEXT_ACTION.v0.1.0.md",
            "pol03_output.schema.json",
            ["action_record", "clarification_requested", "pol01_handoff_ready"]
        ),
        (
            "OUTPUT_AND_TRACE_CONTRACT.v0.1.0.md",
            "agent_turn_output.schema.json",
            ["system_response", "decision_trace", "safety_state", "policy_outputs"]
        )
    ]

    for p_mod, s_name, expected_fields in prompt_schema_checks:
        mod_path = prompt_mods_dir / p_mod
        sch_path = base_dir / "schemas" / s_name
        if not mod_path.exists() or not sch_path.exists():
            continue

        with open(sch_path, "r", encoding="utf-8") as sf:
            sch_data = json.load(sf)
        props = sch_data.get("properties", {})

        for field in expected_fields:
            if "." in field:
                top, sub = field.split(".", 1)
                top_prop = props.get(top, {})
                sub_props = top_prop.get("properties", {})
                if sub not in sub_props:
                    report.error(f"Check 13: Campo nidificato '{field}' citato in prompt '{p_mod}' assente in schema '{s_name}'")
            else:
                if field not in props:
                    report.error(f"Check 13: Campo '{field}' citato in prompt '{p_mod}' assente in schema '{s_name}'")

    # 6.2 Coerenza rigorosa su valori di enum e divieto di termini storici non canonici
    pol01_mod_text = (prompt_mods_dir / "POL-01_IF_THEN_PLAN.v0.1.0.md").read_text(encoding="utf-8")
    if "clarified_with_user" in pol01_mod_text:
        report.error("Check 13: Termine non canonico 'clarified_with_user' ancora presente in POL-01 prompt module")
    if "pol03_handoff" in pol01_mod_text:
        report.error("Check 13: Termine non canonico 'pol03_handoff' ancora presente in POL-01 prompt module")
    if '"user_provided"' not in pol01_mod_text:
        report.error("Check 13: Valore canonico 'user_provided' mancante in POL-01 prompt module")
    if '"pol03_accepted"' not in pol01_mod_text:
        report.error("Check 13: Valore canonico 'pol03_accepted' mancante in POL-01 prompt module")

    pol03_mod_text = (prompt_mods_dir / "POL-03_ACCEPTED_NEXT_ACTION.v0.1.0.md").read_text(encoding="utf-8")
    if "AUTONOMOUS_ACTION_ALREADY_PROVIDED" in pol03_mod_text:
        report.error("Check 13: Motivo non minuscolo 'AUTONOMOUS_ACTION_ALREADY_PROVIDED' in POL-03 prompt module")
    if '"autonomous_action_already_provided"' not in pol03_mod_text:
        report.error("Check 13: Valore canonico 'autonomous_action_already_provided' mancante in POL-03 prompt module")

    with open(base_dir / "schemas" / "pol03_output.schema.json", "r", encoding="utf-8") as f:
        pol03_sch = json.load(f)
    reasons_enum = pol03_sch.get("properties", {}).get("non_activation_reason", {}).get("enum", [])
    if "autonomous_action_already_provided" not in reasons_enum:
        report.error("Check 13: 'autonomous_action_already_provided' mancante nell'enum non_activation_reason di pol03_output.schema.json")

    # 6.3 Coerenza del contratto decision_trace in OUTPUT_AND_TRACE_CONTRACT vs agent_turn_output.schema.json
    contract_path = prompt_mods_dir / "OUTPUT_AND_TRACE_CONTRACT.v0.1.0.md"
    contract_text = contract_path.read_text(encoding="utf-8")
    if re.search(r"\b(lista|array)\s+(ordinata\s+)?di\s+stringhe\b", contract_text, re.IGNORECASE):
        report.error("Check 13: OUTPUT_AND_TRACE_CONTRACT.v0.1.0.md prescrive ancora 'lista di stringhe' anziche oggetti strutturati TraceEvent")

    with open(base_dir / "schemas" / "agent_turn_output.schema.json", "r", encoding="utf-8") as f:
        ato_sch = json.load(f)

    dt_prop = ato_sch.get("properties", {}).get("decision_trace", {})
    if dt_prop.get("type") != "array":
        report.error("Check 13: agent_turn_output.schema.json decision_trace non e' di tipo array")

    for req_field in ["source", "event", "details"]:
        if req_field not in contract_text:
            report.error(f"Check 13: Proprieta obbligatoria '{req_field}' di trace_event mancante in OUTPUT_AND_TRACE_CONTRACT")

    source_enum = []
    if "items" in dt_prop:
        items_def = dt_prop["items"]
        if "$ref" in items_def:
            ref_path = base_dir / "schemas" / items_def["$ref"]
            if ref_path.is_file():
                ref_sch = json.loads(ref_path.read_text(encoding="utf-8"))
                source_enum = ref_sch.get("properties", {}).get("source", {}).get("enum", [])
        else:
            source_enum = items_def.get("properties", {}).get("source", {}).get("enum", [])

    for s_val in source_enum:
        if f'"{s_val}"' not in contract_text:
            report.error(f"Check 13: Valore enum source '{s_val}' mancante in OUTPUT_AND_TRACE_CONTRACT")

    example_match = re.search(r"```json\s*(\{.*?\})\s*```", contract_text, re.DOTALL)
    if not example_match:
        report.error("Check 13: Esempio JSON formale mancante in OUTPUT_AND_TRACE_CONTRACT")
    elif agent_turn_validator:
        try:
            example_data = json.loads(example_match.group(1))
            bundled, _, _ = bundle_schema_with_defs(schemas_dir=base_dir / "schemas")
            baseline_factors = {
                "P1_POL01": False,
                "P2_POL02": False,
                "P3_POL03": False,
            }
            decision_schema = project_model_generated_schema(
                specialize_agent_turn_output_schema(bundled, baseline_factors)
            )
            example_errors = list(Draft202012Validator(decision_schema).iter_errors(example_data))
            for err in example_errors:
                report.error(
                    f"Check 13: Esempio JSON dichiarato conforme in OUTPUT_AND_TRACE_CONTRACT non valido: {err.message}"
                )
        except json.JSONDecodeError as exc:
            report.error(f"Check 13: Esempio JSON malformato in OUTPUT_AND_TRACE_CONTRACT: {exc}")

    if len(report.errors) == lexical_errors_before:
        report.ok("Controllo automatico di coerenza lessicale e semantica prompt-schema verificato con successo")
