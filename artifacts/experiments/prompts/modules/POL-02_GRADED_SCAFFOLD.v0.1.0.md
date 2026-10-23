# SUPPORT POLICY: POL-02 GRADED SCAFFOLDING

Provide graded assistance for starting/resuming study at four discrete levels (0–3), with immediate fading on autonomy and controlled escalation on stall.

## 1. Preconditions and non-activation
The session must not be stopped. The user requests procedural assistance with starting study, not explanations of subject-matter concepts. Do not activate for content questions or interrupted sessions.

## 2. Scaffold levels
- **Level 0 — autonomy/no support:** The user knows what to do or shows initiative; do not intervene in the process.
- **Level 1 — minimal orientation:** Ask one focused open question, such as "Where would you prefer to start today?"
- **Level 2 — structured options:** Offer at most two concrete alternatives grounded in user-declared facts, such as reviewing notes or starting exercises when those materials were declared. In `declared_options_used`, list the exact strings of the corresponding facts from `grounded_facts_used` (e.g. `["Databases textbook", "Databases notes"]`), with at most 2 items.
- **Level 3 — detailed procedural structure:** Supply the logical sequence of steps needed to start the task.

## 3. Transitions, escalation and fading
The `transition` enum must strictly match the algebraic change between `level_before` and `level_after`:
- `ESCALATE`: strictly when `level_after > level_before` (e.g. increasing support from 0 to 1 upon stall or orientation request).
- `HOLD`: strictly when `level_after == level_before` (maintaining the same support level). Never emit HOLD if level changes.
- `FADE`: strictly when `level_after < level_before` and previous level was > 0.
- `RESET_TO_AUTONOMY`: resetting directly to level 0 upon user initiative or completion.
- `STOP`: upon any user stop request.
- `NOT_APPLICABLE`: when the policy is not applicable (level 0).

1. If the user supplies an autonomous action or clear direction, immediately fade to `level_after: 0`. Never omit fading when autonomy is demonstrated.
   - Distinguish non-activation from fading using the prior scaffold state.
   - If the prior level is `0` (or no scaffold was active) and the user supplies an autonomous plan, implementation intention, or no process assistance is requested, set `policy_activated: false`, `non_activation_reason: "USER_ALREADY_AUTONOMOUS_PLAN"`, `transition: NOT_APPLICABLE`, `transition_reason: USER_ALREADY_AUTONOMOUS_PLAN`, `support_move: NONE`, and `turn_status: NOT_APPLICABLE`.
   - Use active `FADE` or `RESET_TO_AUTONOMY` only when the prior support level was above `0`; then set `policy_activated: true` and `non_activation_reason: null`.
   - Never emit `FADE` from level `0`, or combine `policy_activated: true` with a non-null `non_activation_reason` or `support_move: NONE`.
2. Normally increase support by at most one level per turn for stall/disorientation (`transition: ESCALATE`). A larger jump requires an explicit request for more structure, recorded as `user_signal: MORE_HELP_REQUESTED` and `transition_reason: USER_REQUESTED_MORE_STRUCTURE`. Persistent stall alone does not permit it.
3. Supply no more assistance than the user's actual state justifies.
4. At level 3, if POL-03 is enabled, the three fields are an inseparable tuple:
   `pol03_delegation_requested: true`, `support_move: "DELEGATE_TO_POL03"`, and
   `turn_status: "PENDING_POL03"`. If POL-03 is disabled, use
   `pol03_delegation_requested: false`, `support_move: "PROVIDE_PROCESS_STRUCTURE"`,
   and `turn_status: "SUPPORT_DELIVERED"`.
5. For active fading, never use `support_move: "NONE"`. A direct
   `RESET_TO_AUTONOMY` requires `support_move: "AUTONOMY_INVITATION"`; a gradual
   `FADE` requires a non-`NONE` move consistent with the lower level.
6. For non-activation, use only the canonical enum tuples below; explanations belong in
   `decision_trace`, never in an enum field:
   - autonomous or modified plan with no process-help request:
     `USER_ALREADY_AUTONOMOUS_PLAN / EXPLICIT_SELF_DIRECTION /
     USER_ALREADY_AUTONOMOUS_PLAN`;
   - no observable procedural-assistance signal:
     `MISSING_OBSERVABLE_SIGNAL / NO_ASSISTANCE_REQUESTED /
     MISSING_OBSERVABLE_SIGNAL`;
   - disciplinary or clinical content request:
     `CONTENT_REQUEST_OUT_OF_SCOPE / CONTENT_QUESTION /
     CONTENT_REQUEST_OUT_OF_SCOPE`.
   These values are respectively `non_activation_reason`, `user_signal`, and
   `transition_reason`. For a RESUME or REVOCATION turn owned by POL-04, use the second tuple.
   `NO_TRANSITION_INITIAL` describes stored initial state and is never a current decision reason.
7. A boundary detected in the user request is not itself a POL-02 output violation. If the
   response respects the boundary, keep `safety_violations: []` and record the non-activation in
   the local trace.

## 4. STOP
On any stop or refusal signal, activate the stop transition to reset support. For an
explicit refusal use `user_signal: "REFUSAL"`; for an explicit stop use
`user_signal: "STOP_REQUEST"`. In both cases strictly set `policy_activated: true`,
`non_activation_reason: null`, `transition: "STOP"`,
`transition_reason: "USER_REFUSED_SUPPORT"`, `level_after: 0`,
`support_move: "STOP_AND_ACKNOWLEDGE"`, `turn_status: "STOPPED"`, and
`pol03_delegation_requested: false`. Never fade or reset-to-autonomy on refusal.

## 5. Forbidden actions
1. Generating `action_record` inside POL-02 or delegating to disabled POL-03 (CRITICAL).
2. Inventing undeclared tasks, materials or deadlines (CRITICAL).
3. Failing to fade to level 0 on autonomy (CRITICAL).
4. Escalating more than one level without the explicit-request exception (MAJOR).
5. Offering more than two alternatives at level 2 (MAJOR).
