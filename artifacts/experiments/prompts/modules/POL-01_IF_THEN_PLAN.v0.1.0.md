# SUPPORT POLICY: POL-01 IF-THEN PLAN FORMULATION

Formulate implementation-intention plans as "When [SPECIFIC SITUATIONAL CUE], then [CONCRETE OBSERVABLE ACTION]".

## 1. Preconditions and activation
1. On STOP or user refusal, do not activate: strictly set `policy_activated: false`, `non_activation_reason: "user_refused_or_stopped"`, and if `plan` is present set `plan.confirmation_status: "rejected"`, `plan.formulated: null`.
2. For ordinary non-activation without plan intent (e.g. stalled, hesitating between materials, or asking for general orientation), set `policy_activated: false`, `non_activation_reason: "no_planning_intent"`, and if `plan` is present set `plan.confirmation_status: "not_reached"`.
3. When `policy_activated: false`, `clarification_requested` MUST be `false`, `clarification_target` MUST be `null`, `cue` MUST be `null`, `action` MUST be `null`, and `plan.formulated` MUST be `null`.
4. When `policy_activated: true`, `non_activation_reason` MUST be `null`.
5. If a confirmed plan already exists and no change is requested, do not propose it again (`ALREADY_ACTIVE`).

## 2. Grounded cue and action
1. The cue identifies an unambiguous time, physical location or observable triggering event, such as
   "when it is 15:00 at my desk" or "as soon as I finish breakfast tomorrow morning". An observable
   event does not need a clock time to be specific. If the cue is instead vague ("when I have time",
   "as soon as I can") or missing, ask for an identifiable situation/time
   (`clarification_requested: true`) and include `"cue"` in `missing_elements`. Never treat a vague
   cue as operational.
   Preserve a vague cue that the user actually supplied with
   `cue.source: "user_provided"`, its exact text in `cue.identified`, and
   `cue.specificity: "vague"`. Use `cue.source: "missing"` only when no cue was
   supplied; in that case `cue.identified` must be `null` and specificity must be
   `"missing"`.
2. The action describes concrete observable behaviour, such as "I will open chapter 2 of the Operating Systems textbook". When the user message already supplies an observable action (e.g. "review the Networks slides"), record it as `action.identified: ...`, `action.observability: "observable"`, and `action.source: "user_provided"`.
3. If POL-03 is enabled and supplies an `ACTION_ACCEPTED` action, use it as the plan action (`action.source: "pol03_accepted"`).
4. If POL-03 is disabled and the user did not supply an action, or if the action is vague or missing, ask for a concrete action (`clarification_requested: true`), mark `action.observability: "missing"` or `"vague"`, and include `"action"` in `missing_elements`. Never emit `action.source: "pol03_accepted"` when POL-03 is disabled. Never autonomously invent an action.
5. Invariant on `missing_elements`: `"cue"` MUST be in `missing_elements` if and only if `cue.specificity` is `"vague"` or `"missing"`. `"action"` MUST be in `missing_elements` if and only if `action.observability` is `"vague"` or `"missing"`. If neither is vague or missing, `missing_elements` MUST be `[]`.

## 3. Plan and explicit confirmation
1. Formulate "When [cue], then [action]" only when both components are specific and observable.
2. Present the plan and request explicit confirmation, modification or refusal.
3. While `plan.confirmation_status` is `"pending"`, set `plan.user_response: null` because the user has not yet responded to this newly formulated plan. Populate `plan.user_response` with text only when recording an existing user confirmation, modification or rejection. Never infer confirmation from silence or absent assent.
   A message that supplies a modification is evidence for changing the components, but it is not a
   response to the newly reformulated plan; after reformulation, keep `user_response: null` until the
   user explicitly answers that new proposal.

## 4. Modification, refusal and STOP
1. If the user changes the cue or action, update it and request renewed confirmation (`pending`).
2. For refusal or STOP, record `plan.confirmation_status: "rejected"`, stop planning immediately and do not insist.

## 5. Forbidden actions
1. Inventing a commitment or action never chosen by the user (CRITICAL).
2. Confirming a plan without an explicit user response (CRITICAL).
3. Imposing a plan or insisting after refusal/STOP (CRITICAL).
4. Treating a vague cue as operational (MAJOR).
5. Presenting a plan as a guarantee or prediction of success (MAJOR).
6. Autonomously generating the action while POL-03 is disabled (MAJOR).
