# Exploratory held-out quality amendment — 4 October 2026

## Decision and timing

The candidate selected option A ("facciamo A") after the initial human agreement,
development judge comparison and qualitative diagnostic review. No additional human
involvement is requested. This amendment precedes held-out judge dispatch, but follows
producer execution and development measurement inspection. It is not preregistration,
supervisor approval, D07 acceptance or independent validation.

## Authorized route

Score the 1,110 COMPLETED_VALID held-out responses once with the unchanged selected
Luna/OpenAI judge, prompt, rubric and runtime settings. Preserve the full 1,184-slot
producer denominator and all 74 producer failures. Retain every attempted judge failure,
unknown outcome and unstarted item separately; never retry or repair a score silently.
The existing stop-on-failure rule remains in effect. Source binding uses the genuine
producer access capability, not an invented accepted human reference or FinalAccess.
This document separately authorizes exploratory scoring; producer admission alone does not.

The blinded payload must exclude scenario/configuration identities, treatment mapping,
oracle targets and human votes. Lock reconciled ratings before controlled unblinding.
Use the existing finite-benchmark descriptive analysis: five separate ordinal endpoints,
B1 versus B0 and seven factorial contrasts with explicit missingness and denominators.
Do not tune the judge after inspecting held-out scores.

## Interpretation and limits

The original human-reference acceptance rule failed and remains failed. Existing authentic
human scores, agreement estimates, challenge results and diagnostic findings are retained.
The Q1–Q5 outputs are exploratory model-dependent ratings, not human-validated quality,
accuracy against ground truth or calibrated measurements. Shared producer/judge model
bias does not automatically cancel across conditions: treatment-sensitive preferences
and masked-state interpretation errors can distort contrasts. No cross-model robustness,
student effectiveness, population inference or human-reviewed prose-safety claim follows.
Human prose-safety review is not collected in this route; deterministic safety checks
remain separate bounded engineering measurements.

## Evidence locations

- Frozen method and plan: `artifacts/experiments/METHOD_FREEZE_V1.json` and
  `artifacts/experiments/FINAL_INVOCATION_PLAN.json` (unchanged).
- Producer evidence: `artifacts/experiments/runs/evaluation/held_out_final/`.
- Initial human/judge evidence: `artifacts/analysis/human_verification_20261004/`.
- Exploratory bundle/session: `artifacts/experiments/runs/evaluation/held_out_exploratory/`.
- Admission receipt and reconciliation: this directory. Their hashes bind the actual
  method, plan, producer ledger, blinded manifest, sealed mapping and this amendment.
