"""
Orchestratore dell'esecuzione ordinata dei check formali.
Restituisce un ValidationReport senza invocare sys.exit.
"""
from dataclasses import dataclass
from typing import Callable

from .context import ValidationContext
from .report import ValidationReport
from .checks.structure import check_schemas, check_contracts, check_scenarios_schema
from .checks.fixtures import check_fixtures
from .checks.scenarios import check_oracles_and_rubrics, check_scenario_semantics
from .checks.policies import (
    check_policy_separation,
    check_primary_metrics,
    check_inter_rater,
    check_claim_wording,
)
from .checks.benchmark import check_level_b_benchmark, check_level_b_anchors
from .checks.configurations import check_configurations
from .scenario_taxonomy import check_scenario_family_taxonomy


@dataclass(frozen=True)
class CheckSpec:
    number: int
    title: str
    func: Callable[[ValidationContext, ValidationReport], None]


CHECKS: list[CheckSpec] = [
    CheckSpec(1, "Validita dei meta-schemi JSON", check_schemas),
    CheckSpec(2, "Validazione Formale Contratti di Policy (POL-01..POL-05)", check_contracts),
    CheckSpec(3, "Validazione Formale Scenari di Sviluppo e Interazione Cross-Policy", check_scenarios_schema),
    CheckSpec(4, "Validazione Formale Fixture Positive e Negative (POL-01..POL-05)", check_fixtures),
    CheckSpec(5, "Validazione Formale Oracoli Level A e Rubriche degli Scenari", check_oracles_and_rubrics),
    CheckSpec(6, "Validazione semantica di stato e dei metadati statici di traiettoria", check_scenario_semantics),
    CheckSpec(7, "Separazione POL-01 / POL-02 / POL-03 e Invarianti di Handoff/Delega", check_policy_separation),
    CheckSpec(8, "Metriche primarie (M1, M2, M3 in POL-01..POL-05)", check_primary_metrics),
    CheckSpec(9, "Stato e coerenza del protocollo inter-rater (ORACLE-POL01..05)", check_inter_rater),
    CheckSpec(10, "Rigore scientifico del wording nei contratti", check_claim_wording),
    CheckSpec(11, "LEGACY 0.1 benchmark compatibility (not current ordinal acceptance)", check_level_b_benchmark),
    CheckSpec(12, "LEGACY anchor compatibility and anti-circularity checks", check_level_b_anchors),
    CheckSpec(13, "Validazione Formale Configurazioni ($2^3$), Moduli di Prompt e Composizione Deterministica", check_configurations),
    CheckSpec(14, "Development taxonomy coverage (held-out material requires its own reviewed seal)", check_scenario_family_taxonomy),
]


def run_validation(ctx: ValidationContext, report: ValidationReport | None = None) -> ValidationReport:
    if report is None:
        report = ValidationReport()

    for check in CHECKS:
        if report.verbose:
            print(f"\n--- CHECK {check.number}: {check.title} ---")
        try:
            check.func(ctx, report)
        except Exception as e:
            report.error(f"Errore critico durante l'esecuzione di Check {check.number} ({check.title}): {e}")

    return report
