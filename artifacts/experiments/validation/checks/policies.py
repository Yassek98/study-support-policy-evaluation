"""
Check 7, 8, 9, 10: Separazione ruoli, Metriche primarie, Protocollo Inter-Rater e Divieto Claim Vietati.
Governato strettamente dai manifest espliciti con verifiche numeriche.
"""
import json
from ..context import ValidationContext
from ..report import ValidationReport
from ..manifests import (
    POLICY_FILENAMES,
    ORACLE_FILENAMES,
    PROHIBITED_CLAIM_PHRASES,
)


def check_policy_separation(ctx: ValidationContext, report: ValidationReport) -> None:
    policies_dir = ctx.paths.policies
    pol01_data = {}
    pol02_data = {}
    pol03_data = {}
    try:
        if (policies_dir / "POL-01_IF_THEN_PLAN.v0.1.0.json").exists():
            with open(policies_dir / "POL-01_IF_THEN_PLAN.v0.1.0.json", "r", encoding="utf-8") as f:
                pol01_data = json.load(f)
        if (policies_dir / "POL-02_GRADED_SCAFFOLD.v0.1.0.json").exists():
            with open(policies_dir / "POL-02_GRADED_SCAFFOLD.v0.1.0.json", "r", encoding="utf-8") as f:
                pol02_data = json.load(f)
        if (policies_dir / "POL-03_ACCEPTED_NEXT_ACTION.v0.1.0.json").exists():
            with open(policies_dir / "POL-03_ACCEPTED_NEXT_ACTION.v0.1.0.json", "r", encoding="utf-8") as f:
                pol03_data = json.load(f)
    except Exception as e:
        report.error(f"Errore caricamento contratti di policy: {e}")
        return

    # 7.1 POL-01
    p1_rule = pol01_data.get("decision_rule", "").lower()
    if "pol-03" in p1_rule and ("attiva" in p1_rule or "on" in p1_rule):
        report.ok("POL-01: Regola decisionale definisce il comportamento condizionale con POL-03 on vs off")
    else:
        report.error("POL-01: Regola decisionale non formalizza chiaramente il vincolo su POL-03")

    p1_forbidden = " ".join(pol01_data.get("forbidden_actions", [])).lower()
    if "pol-03" in p1_forbidden and "disattiva" in p1_forbidden:
        report.ok("POL-01: Divieto esplicito di generare autonomamente l'azione quando POL-03 e' disattiva")
    else:
        report.error("POL-01: Manca il divieto esplicito di generare l'azione con POL-03 disattivata")

    # 7.2 POL-02
    p2_rule = pol02_data.get("decision_rule", "").lower()
    if "pol-03" in p2_rule and "livello 3" in p2_rule:
        report.ok("POL-02: Regola decisionale definisce la delega a POL-03 condizionata a Livello 3 e policy attiva")
    else:
        report.error("POL-02: Regola decisionale non formalizza la delega condizionata a POL-03")

    p2_forbidden = " ".join(pol02_data.get("forbidden_actions", [])).lower()
    if "action_record" in p2_forbidden and "delega" in p2_forbidden:
        report.ok("POL-02: Divieto esplicito di generare action_record o delegare a POL-03 disattivata")
    else:
        report.error("POL-02: Manca il divieto esplicito di generare action_record in POL-02")

    # 7.3 POL-03
    p3_rule = pol03_data.get("decision_rule", "").lower()
    if "action_accepted" in p3_rule and "pol-01" in p3_rule:
        report.ok("POL-03: Regola decisionale formalizza l'handoff a POL-01 solo su ACTION_ACCEPTED")
    else:
        report.error("POL-03: Regola decisionale non formalizza l'invariante di handoff a POL-01")

    p3_forbidden = " ".join(pol03_data.get("forbidden_actions", [])).lower()
    if "if-then" in p3_forbidden or "cue" in p3_forbidden:
        report.ok("POL-03: Divieto esplicito di formulare piani if-then o generare cue situazionali (responsabilita di POL-01)")
    else:
        report.error("POL-03: Manca il divieto esplicito per POL-03 di formulare piani if-then")


def check_primary_metrics(ctx: ValidationContext, report: ValidationReport) -> None:
    policies_dir = ctx.paths.policies
    for pf_name in POLICY_FILENAMES:
        pf = policies_dir / pf_name
        if not pf.exists():
            continue
        try:
            with open(pf, "r", encoding="utf-8") as f:
                p_data = json.load(f)
            metrics = p_data.get("metrics", [])
            metrics_str = " ".join(metrics).upper()
            pol_id = pf_name.split("_")[0]
            pol_type = p_data.get("policy_type")

            if pol_type == "SAFETY_POLICY":
                # Safety policies (POL-04, POL-05) use signal/boundary discrimination accuracy for M2
                if "M1_CONFORMITY_RATE_APPLICABLE" in metrics_str and "M2_SAFETY_SIGNAL_DISCRIMINATION_ACCURACY" in metrics_str and "M3_CRITICAL_VIOLATION_RATE" in metrics_str:
                    report.ok(f"{pol_id}: Metriche primarie safety formalizzate (M1 conformita, M2 discriminazione segnali, M3 violazioni critiche)")
                else:
                    report.error(f"{pol_id}: Metriche primarie safety non conformi (atteso M1, M2_SAFETY_SIGNAL_DISCRIMINATION_ACCURACY, M3): {metrics}")
            else:
                # Support policies (POL-01..03) use activation accuracy for M2
                if "M1_CONFORMITY_RATE_APPLICABLE" in metrics_str and "M2_ACTIVATION_ACCURACY_ALL" in metrics_str and "M3_CRITICAL_VIOLATION_RATE" in metrics_str:
                    report.ok(f"{pol_id}: Metriche primarie support formalizzate (M1 conformita, M2 accuratezza attivazione, M3 violazioni critiche)")
                else:
                    report.error(f"{pol_id}: Metriche primarie support non conformi alla convenzione tripartita M1/M2/M3: {metrics}")
        except Exception as e:
            report.error(f"Errore verifica metriche {pf_name}: {e}")


def check_inter_rater(ctx: ValidationContext, report: ValidationReport) -> None:
    oracle_dir = ctx.paths.oracles
    quantitative_fields = (
        "sample_size_scenarios",
        "total_scenarios_in_set",
        "percentage_evaluated",
        "min_concordant_units",
        "total_evaluation_units",
        "min_agreement_percentage",
        "unit_of_agreement",
    )
    for of_name in ORACLE_FILENAMES:
        of = oracle_dir / of_name
        if not of.exists():
            continue
        try:
            with open(of, "r", encoding="utf-8") as f:
                o_data = json.load(f)
            ir = o_data.get("inter_rater_protocol", {})
            status = ir.get("status")
            pol_prefix = of_name.replace("ORACLE-", "").split(".")[0]

            if status == "DECISION_PENDING":
                premature_fields = [field for field in quantitative_fields if field in ir]
                if premature_fields:
                    report.error(
                        f"ORACLE-{pol_prefix}: protocollo inter-rater DECISION_PENDING "
                        f"contiene valori quantitativi prematuri: {premature_fields}"
                    )
                else:
                    report.ok(
                        f"ORACLE-{pol_prefix}: protocollo inter-rater DECISION_PENDING "
                        "senza campione, soglia o criterio numerico inventato"
                    )
                continue

            if status != "FROZEN":
                report.error(
                    f"ORACLE-{pol_prefix}: stato inter-rater non riconosciuto: {status!r}"
                )
                continue

            sample_scenarios = ir.get("sample_size_scenarios")
            total_scenarios = ir.get("total_scenarios_in_set")
            pct_eval = ir.get("percentage_evaluated")
            min_conc = ir.get("min_concordant_units")
            tot_units = ir.get("total_evaluation_units")
            min_agr_pct = ir.get("min_agreement_percentage")
            unit_of_agr = ir.get("unit_of_agreement")

            if all(
                value is not None
                for value in (
                    sample_scenarios,
                    total_scenarios,
                    pct_eval,
                    min_conc,
                    tot_units,
                    min_agr_pct,
                    unit_of_agr,
                )
            ):
                # Verifica coerenza percentuale scenari valutati
                calc_eval_pct = (sample_scenarios / total_scenarios) * 100.0
                if abs(calc_eval_pct - pct_eval) > 1e-3:
                    report.error(
                        f"ORACLE-{pol_prefix}: incoerenza percentage_evaluated "
                        f"({sample_scenarios}/{total_scenarios} = {calc_eval_pct:.1f}% != dichiarato {pct_eval}%)."
                    )

                # Verifica coerenza aritmetica dell'accordo
                calc_pct = (min_conc / tot_units) * 100.0
                if calc_pct + 1e-6 < min_agr_pct:
                    report.error(
                        f"ORACLE-{pol_prefix}: incoerenza aritmetica nel protocollo inter-rater "
                        f"({min_conc}/{tot_units} = {calc_pct:.1f}% < soglia {min_agr_pct}%)."
                    )
                else:
                    report.ok(
                        f"ORACLE-{pol_prefix}: Protocollo inter-rater tipizzato e coerente "
                        f"({min_conc}/{tot_units} unita concordanti [{unit_of_agr}], soglia: {min_agr_pct}%)"
                    )
            else:
                report.error(f"ORACLE-{pol_prefix}: campi inter_rater_protocol FROZEN incompleti")
        except Exception as e:
            report.error(f"Errore verifica inter-rater {of_name}: {e}")


def check_claim_wording(ctx: ValidationContext, report: ValidationReport) -> None:
    policies_dir = ctx.paths.policies
    for pf_name in POLICY_FILENAMES:
        pf = policies_dir / pf_name
        if not pf.exists():
            continue
        try:
            with open(pf, "r", encoding="utf-8") as f:
                c_data = json.load(f)
            pol_id = pf_name.split("_")[0]
            operational_body = json.dumps({
                "trigger": c_data.get("trigger"),
                "decision_rule": c_data.get("decision_rule"),
                "observables": c_data.get("observables"),
                "forbidden_actions": c_data.get("forbidden_actions")
            }, ensure_ascii=False).lower()

            for phrase in PROHIBITED_CLAIM_PHRASES:
                if phrase.lower() in operational_body:
                    report.error(f"{pol_id}: Frase vietata trovata nel corpo operativo: '{phrase}'")
                else:
                    report.ok(f"{pol_id}: Frase vietata assente dal corpo operativo: '{phrase}'")
        except Exception as e:
            report.error(f"Errore verifica wording {pf_name}: {e}")
