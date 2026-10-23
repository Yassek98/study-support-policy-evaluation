# Experiment Protocol — Policy Evaluation on a Single Agent

**Version:** 1.1.0 — 2026-09-30, author-review and producer-timing amendment (method snapshot 1.0.6)\
**Status:** `METHOD_SELECTED; PREFLIGHT_REMEDIATION_IN_PROGRESS`\
**Scope:** System behaviour on a finite benchmark; no student-effectiveness claim.\
**Authority:** [Thesis contract](../../meta/THESIS_CONTRACT.md); selected parameters in
[METHOD_FREEZE_V1.json](METHOD_FREEZE_V1.json). Current progress belongs only to
[STATUS.md](../../meta/STATUS.md).

This revision consolidates the selected method without changing RQs, N/R, models, family allocation,
or quality outcomes. It removes superseded instructions, not their provenance. The complete preceding
[protocol](../archive/governance/EXPERIMENT_PROTOCOL_PRE_WP10_2026-09-25.md) and
[decision history](../archive/governance/PROTOCOL_GAP_MAP_PRE_WP10_2026-09-25.md) are preserved
byte-identically; relative links inside those snapshots refer to their original directory.
Software implementation and successful human verification remain distinct from method selection.

## 1. Research questions and evidence boundary

- **RQ1:** How can three literature-grounded candidate support policies for starting or resuming a
  study session be translated into traceable and testable contracts with explicit limits and
  prohibited behaviours?
- **RQ2:** How do the eight combinations of three support policies differ in contract conformity
  and response quality on held-out scenarios?

Level A assesses explicit contract observables and technical outcomes. Level B comprises five
separate ordinal quality criteria. Level C (learning, procrastination reduction, psychological or
clinical benefit) is outside scope. An oracle pass, valid JSON, passing test or successful build
cannot establish human benefit, semantic safety or a calibrated measurement instrument.

The unit executed by the runner is one state-conditioned turn. Prior context is scenario-authored;
trajectory annotations are not an executed multi-turn conversation. MAS, retrieval, persistent
personal memory, unsolicited notifications, a model competition and a jury of judges are excluded.

## 2. Method decision register

These are candidate/internal decisions, not verbatim supervisor approval. The
[10 September record](../meetings/SUPERVISOR_DECISIONS_2026-09-10.md) preserves that distinction.

| ID | Selected method | Evidence still required for execution |
|---|---|---|
| D01 | Q1–Q5 separately; no quality composite or binary quality pass | Correct locked-score reporting |
| D02 | Shared English rubric 0.3.0, anchored 0/1/2 and per-criterion unjudgeability | Human comprehension and reference verification |
| D03 | Formal checks, lexical signals and prose review separate; no extra human terminal classification | Author review of all inspectable final text; explicit coverage and provenance |
| D04 | 74 scenarios, ten families, coverage-derived allocation, equal-family weights | New content, author acceptance and content snapshot; no independent scenario validation |
| D05 | Two draws per cell, not power or precision assurance | Retained actual outcomes and repeat disagreement |
| D06 | Luna/OpenAI producer and operational judge, fixed routes/settings | Applicable execution gates; judge human-reference acceptance |
| D07 | 15 training examples, 80 natural development items, 30 separate challenges; two human streams | Real independent initial votes, reference, diagnostics and acceptance |
| D08 | Descriptive finite-benchmark distributions, B1/B0 pairs and seven factorial contrasts | Complete, failure-aware evidence-to-table integration |
| D09 | Candidate owns funding; one attempt, no fallback, cost receipts, stop on unknown exposure | Per-stage operational allowance and actual receipts, not a new sample-size negotiation |

## 3. Treatments, controls and runtime

| Configuration | POL-01 | POL-02 | POL-03 |
|---|---|---|---|
| B0_SIMPLE | 0 | 0 | 0 |
| S_P1 | 1 | 0 | 0 |
| S_P2 | 0 | 1 | 0 |
| S_P3 | 0 | 0 | 1 |
| G_P1_P2 | 1 | 1 | 0 |
| G_P1_P3 | 1 | 0 | 1 |
| G_P2_P3 | 0 | 1 | 1 |
| B1_FULL | 1 | 1 | 1 |

POL-04 autonomy and POL-05 human-boundary protection remain active in every cell. Each support
factor is the complete versioned bundle of instructions, contract, state/output schemas and
validation burden. Contrasts cannot isolate policy wording or a psychological mechanism.

All cells receive the same current user message and shared prior objective, facts, action,
acceptance evidence and plan. Optional policy state varies; common conversational information does
not. Conditional oracle expectations are declared before generation, never selected from output.
Disabled-policy requirements are not imposed on baseline cells. Missing activation annotations are
unknown, not negative labels. POL-02 normally escalates one level at a time; its explicit-request
exception allows a larger increase. Fading/stop and conditional POL-03 delegation still apply.

Both roles use `openai/gpt-5.6-luna` via `https://openrouter.ai/api/v1/chat/completions`, with
OpenAI pinned (`openai` route), no fallback, required parameter support, and data collection denied
in the routing request. The selector is not a guarantee about provider retention or immutable weights.
Producer: 4,096 output tokens, low reasoning, 30-second timeout, replica seeds 42/43, strict
provider schema plus local semantic validation. Judge: 2,048 output tokens, low reasoning,
30-second timeout, seed 42, ten required flat score/evidence fields projected without repair into
the five-criterion record. Temperature and top-p are omitted for both roles. The seed schedule
does not guarantee independence or reproducibility. Same-model producer/judge bias remains explicit.

Model-owned decisions and text are preserved. Assembly may supply declared runner-owned metadata,
but must reject contradictory decisions instead of replacing them. Duplicate JSON keys, nonfinite
values, missing/extra fields and invalid semantics fail; no coercion or output healing is selected.

## 4. Scenario construction and separation

The [coverage blueprint](taxonomy/BENCHMARK_COVERAGE_PLAN.v0.1.0.json) specifies 37 behavioural
obligations. Two substantive realizations per obligation yield N=74; this is a coverage convention,
not a literature-proven minimum, power calculation or claim of semantic independence. Allocation
F01–F10 is **6, 8, 6, 6, 10, 10, 6, 4, 6, 12**. Scenarios in a family have equal weight;
each family receives 1/10 of an overall quality summary. Each scenario has two draws in all eight
cells: **1,184 planned producer slots**, and at most that many ordinary judge attempts.

Keep development, training, natural verification, anchor challenges and final held-out evidence
distinct. Content/identity/lineage manifests detect explicit overlap; human review must check
substantive diversity and reworded duplicates. Development failures, templates or edited scenarios
do not become new held-out evidence by renaming them. No final response may tune the instrument.

On 30 September the candidate removed the independent scenario-review requirement.
The 74 scenarios are accepted by their author; retain those authentic judgments and
bind them to the exact scenario content before execution. The candidate reports supervisor
acceptance of the generation method, not independent approval of each scenario.
Author confirmation cannot rule out author bias, unrealistic cases or semantic duplication;
this is a limitation of the finite benchmark. Do not claim independent ground-truth validation.
Any change to the selected allocation requires an explicit method amendment. Each scenario binds
its family, oracle, applicable/forbidden behaviours, expected observables and source rationale.
Fingerprint the reviewed package before execution. Specialist review of psychological assumptions
is a different task; neither scenario review nor response rating silently supplies it.

## 5. Shared measurement and human-reference procedure

The [English rubric](evaluation/rubrics/START_RESUME_QUALITY.v0.3.0.json) defines request alignment,
fact grounding, next-state usability, proportionality/readability and state-dialogue consistency.
Humans and judge see the same substantive rubric, context, response and normalized common states.
Policy/configuration labels, oracle answers and other raters' scores are absent. Wording can still
reveal a policy: this is label blinding, not guaranteed anonymity. Instructions inside a response
remain quoted data, not new evaluator instructions.

Each criterion receives 0, 1, 2, or null with a nonblank criterion-specific reason. Null is neither
a middle score nor a failure. Human comments are otherwise optional; judge evidence is required
for every criterion. Q2/Q5 overlap is declared, not assumed away. There is no extra human outcome
classification and no conversion to `PASS_LEVEL_B` or composite `PASS_VALID`.

### D07 sample and independent reference

1. **Training:** 15 worked examples, one per criterion/category pair; not verification evidence.
2. **Natural verification:** 80 English development responses, one eligible item from every
   family/configuration stratum. Select by recorded seed/hash from a version-bound complete producer
   inventory, without looking at scores. Preserve all source failures. An empty stratum blocks
   acceptance; do not substitute another family or configuration.
3. **Challenges:** 30 new independently reviewed items, two per criterion/category pair. Do not
   recycle training by rewording. Targets stay sealed from ordinary raters and judge. Both humans
   score all five criteria, but only the declared target is used for challenge acceptance. Keep
   these results separate from natural-item agreement/accuracy.
4. **Humans:** author-associated and one external rater independently score the same 110 items:
   550 criterion judgments per person, plus training, discussion and adjudication. Reviewer English
   competence, role, development involvement and capacity must be documented, not inferred from
   headcount. These response ratings are distinct from author acceptance of scenarios.
5. **Reference:** retain initial votes before discussion. Establish an auditable reference without
   seeing judge scores; unresolved/challenged references block acceptance. Reviewing agreed cases
   matters too, because agreement can reflect shared error.

For each natural criterion, report ordinal Krippendorff alpha, exact agreement, marginals,
confusion tables, missing ratings and extreme errors. Require **alpha >= .80** for human/human
and separately judge/adjudicated-reference comparison, no natural judge/reference 0-versus-2
errors, and matches to independently reviewed challenge targets. This is a project convention
supported cautiously by Artstein and Poesio, section 5.3, not a universal validity law.
Non-estimable alpha, parser failures, incomplete ratings, uncovered strata or unresolved reference
judgments do not pass. Adjudication cannot manufacture initial human agreement.

One documented correction may use an equivalently designed **new** verification set; the former
set becomes development-only. A second failure or inability to obtain the required material stops
automatic promotion and requires a documented human-led fallback or justified redesign. Do not
lower the threshold, add a jury or search models after final outcomes. Timing after held-out
preparation does not change the development-only source of this verification.

## 6. Separate safety review

Author review covers all inspectable final response text, including readable invalid model outputs.
Keep raw-source identity/hash, reviewer role, evidence, violation codes, review status and disagreement.
Unreadable text is uninspectable; missing review is unreviewed. Neither absent model flags nor
absent lexical matches constitute reviewed safety. Signals are referrals, not findings.

Formal checks, lexical flags and prose review have different inspected populations. Report each
coverage denominator explicitly. A separately assigned independent review is required for an
independent safety claim. Severe findings are non-compensatory, but do not erase retained quality
observations or turn all five ratings into zero. There is no composite success endpoint.

## 7. Execution, interruptions and accounting

Freeze an explicit Cartesian invocation plan before dispatch. Each row records scenario,
configuration, replication, batch and protocol identity. One attempt is permitted per row, with
zero technical/quality retry, fallback or healing. Generic retry tests do not enable final retries.
Persist intent before dispatch and retain request, provider envelope, model output, canonical
record when valid, hashes, telemetry and cost. Unknown cost remains unknown, not zero.

Reconciliation distinguishes **NOT_STARTED**, **OUTCOME_UNKNOWN**, and retained terminal results,
including provider/schema/semantic failures. An empty/partial claim is not permission to repeat
the call. A retained result can survive a missing completion receipt. Recover already-persisted
judge scores without new calls; retain not-started judge items as absent. A judge failure or unknown
exposure stops its batch. Closing a stopped stream preserves missingness; continuation may include
only genuinely unstarted slots through a reviewed procedure, never a whole-bundle rerun for quality.

A systemic software/environment defect stops execution and requires an explicit amendment with
all affected evidence retained. There is no standing whole-suite replacement permission. A local
claim store does not enforce exactly-once behaviour across copied roots; keep one canonical store.

Ten runner terminal outcomes remain distinct: valid completion; malformed, schema-invalid and
semantically invalid model responses; transport error, timeout, provider error, invalid run record,
internal runner error and input-contract error. Only `COMPLETED_VALID` enters ordinary quality
scoring. That label means an ingestible record, not a policy pass or good response.

## 8. Analysis and denominators

For configuration j retain D_plan=74*2, D_model (returned model payloads, including invalid ones)
and D_eval (valid canonical records). Missing terminal observations after interruption remain
explicit and block a complete analysis until resolved or a documented deviation is adopted.
Every technical outcome remains in its planned denominator.

- **M1:** per-active-policy conformity among explicitly applicable, oracle-covered returned
  payloads. Invalid applicable model artifacts contribute zero; no-payload calls remain reliability
  failures. Policy-off cells are not assigned fictitious conformity scores.
- **M2:** correct contract-defined activation among returned payloads with an explicit positive or
  negative activation annotation. Applicability alone is not an activation label; report coverage.
- **M3:** critical safety findings over the named inspectable subset, with coverage relative to
  returned payloads. Formal and prose-reviewed populations are separate.
- **Q1–Q5:** category counts/masses over planned slots plus separately labelled distributions over
  rated slots. Distinguish producer ineligibility, absent rating, criterion unjudgeability and judge
  failure; provider failure retains its more specific technical code.

Average within-family quality summaries with weight 1/10. Within a family, scenario weights and
replication weights are equal. If any family has no rated observations, its conditional macro
estimate is unavailable rather than renormalized over surviving families. Zero denominators are
unavailable (`null`/`NOT_ESTIMABLE`), never perfect performance or zero quality.

Primary comparison: matched B1 versus B0 higher/equal/lower counts within scenario and replication
slot, with planned, paired and missing counts and equal-family summaries. A slot match is not
evidence of common random numbers. Secondary contrasts use complete eight-cell blocks for each
criterion/category indicator. With factor signs z=-1/+1, beta_S=(1/8)*sum_j product(z_pj)*Y_j
for the seven nonempty factor subsets. A one-factor high-minus-low contrast is 2*beta, not beta.
Report all three categories (their coefficients sum to zero), complete block identities, lost
coverage and family-stratified coefficients; macro coefficients are unavailable if any family
has no complete block. Do not impute missing scores or average the five criteria together.

Report repeat-slot disagreement, human/judge disagreement, missingness sensitivity, latency,
request/input/output-token burden and actual cost, without hiding failed calls. No population
confidence interval, significance test, equivalence margin or minimum-effect claim is selected.
Observed similarity, degradation and mixed trade-offs are legitimate finite-benchmark findings.

## 9. Freeze and promotion gates

The method snapshot binds the conservative executable dependency inventory, including code,
schemas, policies, prompts, current instrument, fixtures/development inputs and dependency lock.
Method text hashes normalize UTF-8 line endings to LF so an unchanged Git checkout remains comparable;
raw requests, provider responses and retained experimental evidence keep byte-exact hashes.
It is not the held-out seal, installed-environment attestation or proof of scientific validity.
The reviewed final package needs its own identity/content/review manifest. Commit, tag and push
remain candidate-owned; no current release tag is claimed merely because a hash file exists.

Required order: close preflight remediation; construct and author-review new scenarios;
snapshot the accepted final package; run final producers. Human-reference collection and
judge verification on separate development material may proceed alongside production.
Final automatic scoring still requires D07 acceptance; otherwise select the explicit human-led
fallback. Then collect blind scores, lock them, authorize label unblinding and produce the
failure-aware analysis. No held-out tuning or automatic gate promotion. The 30 September
amendment changes scenario review and production timing only, not runtime, prompts, N/R or rubric.

## 10. Rationale and historical evidence

The [method evidence map](PROTOCOL_GAP_MAP.md) links source notes and alternatives. The source
literature motivates coverage, careful sample-size justification, shared rubric definition and
reliability diagnostics; it does not prove this software effective or mandate N=74, R=2, three
rating categories or the chosen alpha threshold.

The WP9 record of 328 development cells means at least one valid response per cell across controlled
amendments, not one 328/328 run on a single final fingerprint. Historical model probes, costs,
rubric versions and source observations remain in their original records and archived protocol.
They must not be silently upgraded into current calibration or final policy evidence.
