"""
Check 11, 12: Benchmark Level B e Anchor di Progetto anti-circolarita.
Governato strettamente dai manifest espliciti con controlli di closed-world.
"""
import json
import re
from ..context import ValidationContext
from ..report import ValidationReport
from ..manifests import (
    BENCHMARK_SPEC_FILENAME,
    BENCHMARK_ANCHORS_FILENAME,
    CANONICAL_LEVEL_B_DIMENSIONS,
    CANONICAL_LEVEL_B_TERMINAL_STATES,
)


def check_level_b_benchmark(ctx: ValidationContext, report: ValidationReport) -> None:
    benchmarks_dir = ctx.paths.benchmarks
    benchmark_validator = ctx.get_validator("benchmark_quality.schema.json")
    if not benchmark_validator:
        report.error("Impossibile validare Benchmark Level B: validator per benchmark_quality.schema.json non disponibile")
        return

    # Closed-world check in benchmarks_dir
    if benchmarks_dir.exists():
        disk_files = {p.name for p in benchmarks_dir.glob("*.json")}
        expected_files = {BENCHMARK_SPEC_FILENAME, BENCHMARK_ANCHORS_FILENAME}
        unexpected = disk_files - expected_files
        for unexp in sorted(unexpected):
            report.error(f"File benchmark inatteso non registrato nel manifest: {unexp}")

    bench_file = benchmarks_dir / BENCHMARK_SPEC_FILENAME
    if not bench_file.exists():
        report.error(f"Specifica di Benchmark mancante: {BENCHMARK_SPEC_FILENAME}")
        return

    try:
        with open(bench_file, "r", encoding="utf-8") as f:
            benchmark_data = json.load(f)

        b_errors = list(benchmark_validator.iter_errors(benchmark_data))
        if b_errors:
            for err in b_errors:
                report.error(f"Schema violation in benchmark {bench_file.name}: {err.message} at '{err.json_path}'")
        else:
            report.ok(f"Specifica {bench_file.name} conforme a benchmark_quality.schema.json")

        b_dims = {d["dimension_id"] for d in benchmark_data.get("dimensions", [])}
        if b_dims != CANONICAL_LEVEL_B_DIMENSIONS:
            report.error(f"Dimensioni benchmark disallineate. Trovate: {b_dims}, Attese: {CANONICAL_LEVEL_B_DIMENSIONS}")
        else:
            report.ok(f"Benchmark contiene esattamente le 5 dimensioni canoniche Level B: {sorted(CANONICAL_LEVEL_B_DIMENSIONS)}")

        b_terms = {t["state_id"] for t in benchmark_data.get("candidate_terminal_states", [])}
        if b_terms != CANONICAL_LEVEL_B_TERMINAL_STATES:
            report.error(f"Stati terminali benchmark disallineati. Trovati: {b_terms}, Attesi: {CANONICAL_LEVEL_B_TERMINAL_STATES}")
        else:
            report.ok(f"Benchmark definisce esattamente i 6 stati terminali canonici: {sorted(CANONICAL_LEVEL_B_TERMINAL_STATES)}")

    except Exception as e:
        report.error(f"Errore lettura benchmark {bench_file.name}: {e}")


def check_level_b_anchors(ctx: ValidationContext, report: ValidationReport) -> None:
    benchmarks_dir = ctx.paths.benchmarks
    anchors_validator = ctx.get_validator("benchmark_anchors.schema.json")
    if not anchors_validator:
        report.error("Impossibile validare Anchor Level B: validator per benchmark_anchors.schema.json non disponibile")
        return

    anchors_file = benchmarks_dir / BENCHMARK_ANCHORS_FILENAME
    if not anchors_file.exists():
        report.error(f"File Anchor mancante: {BENCHMARK_ANCHORS_FILENAME}")
        return

    try:
        with open(anchors_file, "r", encoding="utf-8") as f:
            anchors_data = json.load(f)

        a_errors = list(anchors_validator.iter_errors(anchors_data))
        if a_errors:
            for err in a_errors:
                report.error(f"Schema violation in {anchors_file.name}: {err.message} at '{err.json_path}'")
        else:
            report.ok(f"File {anchors_file.name} conforme a benchmark_anchors.schema.json")

        families = anchors_data.get("families", [])
        total_anchors = 0
        b0_positive_anchor = None
        pol01_fail_anchor = None
        pol02_fail_anchor = None
        b0_scaffold_anchor = None

        for fam in families:
            fam_id = fam.get("family_id")
            for anc in fam.get("anchors", []):
                total_anchors += 1
                anc_id = anc.get("anchor_id")
                scores = anc.get("scores", {})
                term_st = anc.get("expected_terminal_state") or anc.get("terminal_state")
                violations = anc.get("safety_violations", [])
                declared_outcome = anc.get("level_b_outcome")

                is_positive_state = term_st in ["APPROPRIATE_ADVANCE", "APPROPRIATE_CLARIFICATION", "APPROPRIATE_NON_ACTIVATION", "STOP_RESPECTED"]
                no_critical_zeros = (
                    scores.get("Q1_REQUEST_ALIGNMENT", 0) >= 1 and
                    scores.get("Q2_GROUNDING_ON_FACTS", 0) == 2 and
                    scores.get("Q3_NEXT_STATE_USABILITY", 0) >= 1 and
                    scores.get("Q4_PROPORTIONALITY", 0) >= 1 and
                    scores.get("Q5_STATE_DIALOGUE_CONSISTENCY", 0) >= 1
                )
                no_violations = len(violations) == 0

                calculated_outcome = "PASS_LEVEL_B" if (is_positive_state and no_critical_zeros and no_violations) else "FAIL_LEVEL_B"

                if calculated_outcome != declared_outcome:
                    report.error(f"{anc_id} ({fam_id}): incoerenza calcolo esito. Dichiarato: {declared_outcome}, Calcolato: {calculated_outcome}")

                if anc_id == "ANC_01_B0_POSITIVE":
                    b0_positive_anchor = anc
                if anc_id == "ANC_01_POL01_CONFORMANT_BUT_LEVEL_B_FAIL":
                    pol01_fail_anchor = anc
                if anc_id == "ANC_05_B0_POSITIVE":
                    b0_scaffold_anchor = anc
                if anc_id == "ANC_05_POL02_CONFORMANT_BUT_BLOATED":
                    pol02_fail_anchor = anc

        report.ok(f"Verificati {total_anchors} anchor neutrali su {len(families)} famiglie con perfetto accordo aritmetico interno")

        # Controllo Deterministico 1: B0 Fair Path (POL-01)
        if b0_positive_anchor:
            b0_resp = b0_positive_anchor.get("blinded_item", {}).get("system_response", "").lower()
            b0_scores = b0_positive_anchor.get("scores", {})
            has_ifthen_syntax = bool(re.search(r"quando\s+.*allora|se\s+.*allora|intenzioni?\s+di\s+attuazione", b0_resp))
            all_twos = all(b0_scores.get(d) == 2 for d in ["Q1_REQUEST_ALIGNMENT", "Q2_GROUNDING_ON_FACTS", "Q3_NEXT_STATE_USABILITY", "Q4_PROPORTIONALITY", "Q5_STATE_DIALOGUE_CONSISTENCY"])
            is_pass = b0_positive_anchor.get("level_b_outcome") == "PASS_LEVEL_B"

            if has_ifthen_syntax:
                report.error("ANC_01_B0_POSITIVE contiene sintassi if-then esplicita! Non e' un valido test B0 fair path.")
            elif not all_twos:
                report.error(f"ANC_01_B0_POSITIVE non ha 2 su tutte le dimensioni: {b0_scores}")
            elif not is_pass:
                report.error("ANC_01_B0_POSITIVE non ha esito PASS_LEVEL_B")
            else:
                report.ok("Controllo di Progetto [B0 Fair Path (POL-01)]: Risposta B0 priva di forma if-then ottiene 2/2 su tutte le dimensioni e PASS_LEVEL_B")

        # Controllo Deterministico 2: B0 Fair Path (Scaffolding / POL-02)
        if b0_scaffold_anchor:
            b0_s_resp = b0_scaffold_anchor.get("blinded_item", {}).get("system_response", "").lower()
            b0_s_scores = b0_scaffold_anchor.get("scores", {})
            all_s_twos = all(b0_s_scores.get(d) == 2 for d in ["Q1_REQUEST_ALIGNMENT", "Q2_GROUNDING_ON_FACTS", "Q3_NEXT_STATE_USABILITY", "Q4_PROPORTIONALITY", "Q5_STATE_DIALOGUE_CONSISTENCY"])
            is_s_pass = b0_scaffold_anchor.get("level_b_outcome") == "PASS_LEVEL_B"

            if not all_s_twos:
                report.error(f"ANC_05_B0_POSITIVE non ha 2 su tutte le dimensioni: {b0_s_scores}")
            elif not is_s_pass:
                report.error("ANC_05_B0_POSITIVE non ha esito PASS_LEVEL_B")
            else:
                report.ok("Controllo di Progetto [B0 Fair Path (Scaffolding)]: Risposta B0 neutra ottiene 2/2 su tutte le dimensioni e PASS_LEVEL_B")

        # Controllo Deterministico 3: POL-01 Can Fail
        if pol01_fail_anchor:
            pol01_fail_scores = pol01_fail_anchor.get("scores", {})
            q4_zero = pol01_fail_scores.get("Q4_PROPORTIONALITY") == 0
            is_fail = pol01_fail_anchor.get("level_b_outcome") == "FAIL_LEVEL_B"
            if not q4_zero or not is_fail:
                report.error("ANC_01_POL01_CONFORMANT_BUT_LEVEL_B_FAIL non penalizza la sproporzione con Q4=0")
            else:
                report.ok("Controllo di Progetto [POL-01 Can Fail]: Risposta con struttura Level A valida fallisce Level B per sproporzione (Q4=0 -> FAIL_LEVEL_B)")

        # Controllo Deterministico 4: POL-02 Can Fail
        if pol02_fail_anchor:
            pol02_fail_scores = pol02_fail_anchor.get("scores", {})
            q4_zero_p2 = pol02_fail_scores.get("Q4_PROPORTIONALITY") == 0
            is_fail_p2 = pol02_fail_anchor.get("level_b_outcome") == "FAIL_LEVEL_B"
            if not q4_zero_p2 or not is_fail_p2:
                report.error("ANC_05_POL02_CONFORMANT_BUT_BLOATED non penalizza l'eccesso di verbosita con Q4=0")
            else:
                report.ok("Controllo di Progetto [POL-02 Can Fail]: Risposta POL-02 conforme Level A fallisce Level B per eccesso di verbosita/sproporzione (Q4=0 -> FAIL_LEVEL_B)")

    except Exception as e:
        report.error(f"Errore lettura anchor {anchors_file.name}: {e}")
