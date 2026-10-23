"""
Check 1, 2, 3: Validita dei meta-schemi JSON, contratti di policy e conformita strutturale degli scenari.
Governato strettamente dai manifest espliciti, tabelle canoniche e controlli closed-world.
"""
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from ..context import ValidationContext
from ..report import ValidationReport
from ..manifests import (
    SCHEMA_FILENAMES,
    POLICY_FILENAMES,
    ALL_DEVELOPMENT_SCENARIOS,
)

CANONICAL_POLICY_CONFIG = {
    "POL-01_IF_THEN_PLAN": {
        "policy_type": "SUPPORT_POLICY",
        "output_schema_ref": "schemas/pol01_output.schema.json",
        "allowed_claims": {"CLM-01", "CLM-02", "CLM-03", "CLM-04"},
    },
    "POL-02_GRADED_SCAFFOLD": {
        "policy_type": "SUPPORT_POLICY",
        "output_schema_ref": "schemas/pol02_output.schema.json",
        "allowed_claims": {"CLM-04", "CLM-05", "CLM-06", "CLM-07", "CLM-08"},
    },
    "POL-03_ACCEPTED_NEXT_ACTION": {
        "policy_type": "SUPPORT_POLICY",
        "output_schema_ref": "schemas/pol03_output.schema.json",
        "allowed_claims": {"CLM-01", "CLM-02", "CLM-04", "CLM-06", "CLM-08", "CLM-09"},
    },
    "POL-04_AUTONOMY_GATE": {
        "policy_type": "SAFETY_POLICY",
        "output_schema_ref": "schemas/safety_envelope_output.schema.json",
        "allowed_claims": {"CLM-06", "CLM-09", "CLM-10"},
    },
    "POL-05_HUMAN_BOUNDARY": {
        "policy_type": "SAFETY_POLICY",
        "output_schema_ref": "schemas/safety_envelope_output.schema.json",
        "allowed_claims": {"CLM-10", "CLM-11"},
    },
}


def check_schemas(ctx: ValidationContext, report: ValidationReport) -> None:
    schemas_dir = ctx.paths.schemas

    # Controllo closed-world: nessun file .json non registrato nella cartella schemas
    if schemas_dir.exists():
        disk_files = {p.name for p in schemas_dir.glob("*.json")}
        manifest_files = set(SCHEMA_FILENAMES)
        unexpected = disk_files - manifest_files
        for unexp in sorted(unexpected):
            report.error(f"File schema inatteso non registrato nel manifest: {unexp}")

    loaded_schemas = {}
    for sf_name in SCHEMA_FILENAMES:
        sf = schemas_dir / sf_name
        if not sf.exists():
            report.error(f"File di schema obbligatorio mancante: {sf_name}")
            continue
        try:
            with open(sf, "r", encoding="utf-8") as f:
                s_data = json.load(f)
            Draft202012Validator.check_schema(s_data)
            loaded_schemas[sf_name] = s_data
            report.ok(f"Meta-schema valido (Draft 2020-12): {sf_name}")
        except Exception as e:
            report.error(f"Errore nel meta-schema {sf_name}: {e}")

    registry = Registry()
    for s_name, s_data in loaded_schemas.items():
        try:
            res = Resource.from_contents(s_data, default_specification=DRAFT202012)
            s_id = s_data.get("$id", s_name)
            registry = registry.with_resource(s_id, res)
            registry = registry.with_resource(s_name, res)
            registry = registry.with_resource(f"https://local.thesis.invalid/schemas/{s_name}", res)
            registry = registry.with_resource(f"schemas/{s_name}", res)
            registry = registry.with_resource(f"../schemas/{s_name}", res)
        except Exception as e:
            report.error(f"Errore registrazione schema {s_name} nel registry: {e}")

    loaded_validators = {}
    for s_name, s_data in loaded_schemas.items():
        try:
            loaded_validators[s_name] = Draft202012Validator(
                s_data,
                registry=registry,
                format_checker=Draft202012Validator.FORMAT_CHECKER
            )
        except Exception as e:
            report.error(f"Errore istanziazione validator per {s_name}: {e}")

    ctx.raw_schemas = loaded_schemas
    ctx.registry = registry
    ctx.validators = loaded_validators


def check_contracts(ctx: ValidationContext, report: ValidationReport) -> None:
    policies_dir = ctx.paths.policies
    policy_schema_validator = ctx.get_validator("policy_contract.schema.json")
    if not policy_schema_validator:
        report.error("Impossibile validare contratti di policy: validator per policy_contract.schema.json non disponibile")
        return

    # Controllo closed-world: nessun file policy non registrato
    if policies_dir.exists():
        disk_files = {p.name for p in policies_dir.glob("*.json")}
        manifest_files = set(POLICY_FILENAMES)
        unexpected = disk_files - manifest_files
        for unexp in sorted(unexpected):
            report.error(f"File policy inatteso non registrato nel manifest: {unexp}")

    for pf_name in POLICY_FILENAMES:
        pf = policies_dir / pf_name
        if not pf.exists():
            report.error(f"File di policy obbligatorio mancante: {pf_name}")
            continue
        try:
            with open(pf, "r", encoding="utf-8") as f:
                p_data = json.load(f)

            # 1. Validazione schema Draft 2020-12
            p_errors = list(policy_schema_validator.iter_errors(p_data))
            if p_errors:
                for err in p_errors:
                    report.error(f"Contratto {pf_name} non valido rispetto al meta-schema: {err.message}")
            else:
                report.ok(f"Contratto di policy conforme allo schema (Level A): {pf_name}")

            # 2. Referential integrity & identity checks
            pol_id = p_data.get("policy_id")
            ver = p_data.get("version")
            status = p_data.get("status")
            pol_type = p_data.get("policy_type")
            schema_ref = p_data.get("output_schema_ref")
            claims = p_data.get("evidence_claims", [])

            # Check filename ↔ policy_id ↔ version
            expected_prefix = f"{pol_id}.v{ver}.json"
            if pf_name != expected_prefix:
                report.error(
                    f"Contratto {pf_name}: disallineamento nome file. "
                    f"Atteso '{expected_prefix}', trovato '{pf_name}'."
                )

            # Check version and status
            if ver != "0.1.0":
                report.error(f"Contratto {pf_name}: version '{ver}' non canonica (atteso '0.1.0').")
            if status != "CANDIDATE":
                report.error(f"Contratto {pf_name}: status non canonico '{status}' (atteso 'CANDIDATE').")

            # Check canonical policy configuration
            cfg = CANONICAL_POLICY_CONFIG.get(pol_id)
            if not cfg:
                report.error(f"Contratto {pf_name}: policy_id '{pol_id}' non presente nella mappa canonica.")
                continue

            if pol_type != cfg["policy_type"]:
                report.error(f"Contratto {pf_name}: policy_type '{pol_type}' errato (atteso '{cfg['policy_type']}').")

            if schema_ref != cfg["output_schema_ref"]:
                report.error(
                    f"Contratto {pf_name}: output_schema_ref '{schema_ref}' disallineato dalla configurazione canonica "
                    f"(atteso '{cfg['output_schema_ref']}')."
                )

            # Check physical existence of output_schema_ref on disk
            if schema_ref:
                ref_path = ctx.paths.base / schema_ref
                if not ref_path.exists():
                    report.error(f"Contratto {pf_name}: output_schema_ref '{schema_ref}' non esiste su disco.")
                else:
                    report.ok(f"Contratto {pf_name}: output_schema_ref '{schema_ref}' verificato su disco")

            # Check valid claim IDs and canonical mapping
            if not claims or len(claims) == 0:
                report.error(f"Contratto {pf_name}: evidence_claims non puo essere vuoto.")

            claims_set = set(claims)
            allowed_claims = cfg["allowed_claims"]
            unallowed = claims_set - allowed_claims
            if unallowed:
                report.error(f"Contratto {pf_name}: claims non pertinenti {sorted(unallowed)} (ammessi: {sorted(allowed_claims)}).")
            else:
                report.ok(f"Contratto {pf_name}: claims pertinenti e conformi: {sorted(claims_set)}")

        except Exception as e:
            report.error(f"Errore lettura {pf_name}: {e}")


def check_scenarios_schema(ctx: ValidationContext, report: ValidationReport) -> None:
    scenarios_dir = ctx.paths.development_scenarios
    scenario_schema_validator = ctx.get_validator("scenario.schema.json")
    if not scenario_schema_validator:
        report.error("Impossibile validare scenari: validator per scenario.schema.json non disponibile")
        return

    # Controllo closed-world: nessun file scenario non registrato nel manifest
    if scenarios_dir.exists():
        disk_files = {p.name for p in scenarios_dir.glob("*.json")}
        manifest_files = set(ALL_DEVELOPMENT_SCENARIOS)
        unexpected = disk_files - manifest_files
        for unexp in sorted(unexpected):
            report.error(f"Scenario inatteso non registrato nel manifest: {unexp}")

    for sc_name in ALL_DEVELOPMENT_SCENARIOS:
        sc_file = scenarios_dir / sc_name
        if not sc_file.exists():
            report.error(f"Scenario obbligatorio mancante dal disco: {sc_name}")
            continue
        try:
            with open(sc_file, "r", encoding="utf-8") as f:
                sc_data = json.load(f)
            sc_errors = list(scenario_schema_validator.iter_errors(sc_data))
            if sc_errors:
                for err in sc_errors:
                    report.error(f"Scenario {sc_name} non conforme a scenario.schema.json: {err.message}")
            else:
                report.ok(f"Scenario conforme allo schema: {sc_name}")
        except Exception as e:
            report.error(f"Errore lettura scenario {sc_name}: {e}")
