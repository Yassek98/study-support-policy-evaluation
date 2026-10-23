# SUPPORT POLICY: POL-03 ACCEPTED NEXT ACTION

Propose and verify one concrete next micro-action for starting/resuming study when the user does not already have one.

## 1. Preconditions and non-activation
1. The session must not be stopped and there must not already be an accepted action (`action_record.action_status != "ACTION_ACCEPTED"`).
2. If the user has independently supplied a concrete next study action, do not activate (`policy_activated: false`, reason `"autonomous_action_already_provided"`). Leave that action to common handling and/or direct consumption by POL-01.
3. When `policy_activated: false`, set a non-null `non_activation_reason`, and strictly set `turn_status: "NOT_APPLICABLE"`, `clarification_requested: false`, `clarification_prompt: null`, `proposed_action: null`, `action_count: 0`, `pol01_handoff_ready: false`, and in `action_record` set `explicit_user_evidence: null` and `accepted_version: null`.
4. When `policy_activated: true`, `non_activation_reason` MUST be `null`.
5. If POL-01 is active and requests clarification because the action is missing or vague, POL-03
   is also active but cannot propose an action yet. Set `clarification_requested: true`,
   `turn_status: "PENDING_CLARIFICATION"`, `proposed_action: null`, and keep the action record
   unassigned. Never combine `clarification_requested: true` with `policy_activated: false` or
   `turn_status: "NOT_APPLICABLE"` on this path.
6. Do not propose new actions when an accepted, unchanged action already exists.
7. An existing `ACTION_PROPOSED` record is still an active POL-03 decision until
   the user accepts, modifies, or rejects it. Preserve its identifier, text,
   source, and grounded facts; set `policy_activated: true`,
   `non_activation_reason: null`, `turn_status: "ACTION_PROPOSED_PENDING_CONFIRMATION"`,
   and keep that same single action as `proposed_action`. Request confirmation in
   `system_response`, not in `clarification_prompt`: because no grounding fact is
   missing, set `clarification_requested: false` and `clarification_prompt: null`.

## 2. One micro-action
1. If the action is absent but declared facts suffice, formulate exactly one concrete, low-load, immediately executable proposal, such as opening the declared slide file at the declared page (`action_count == 1`).
2. Record the proposal as `ACTION_PROPOSED`, with `accepted_version: null`.
   Whenever `proposed_action` is non-null, it must be character-for-character identical to
   `action_record.action_text`; use the same canonical string in both fields rather than paraphrasing
   one of them.
3. Clarification is requested ONLY when the policy is ACTIVE (`policy_activated: true`). If facts do not suffice for a grounded action, request a focused clarification (`clarification_requested: true`). Never invent missing details. Never set `clarification_requested: true` when `policy_activated: false`.

## 3. Acceptance and handoff
1. Transition to `ACTION_ACCEPTED` only with unambiguous textual user confirmation (`explicit_user_evidence`). In all other statuses (such as `NOT_APPLICABLE`, `STOPPED`, `ACTION_PROPOSED`, or `PENDING_CLARIFICATION`), `explicit_user_evidence` in `action_record` and top-level `user_evidence_recorded` MUST be `null`.
2. If the user modifies the proposed action, the resulting `action_version` increases and the status remains `ACTION_PROPOSED` until renewed explicit confirmation.
3. Enable `pol01_handoff_ready: true` only for `ACTION_ACCEPTED`.

## 4. Refusal and STOP
1. When an existing pending POL-03 proposal is explicitly refused, activate the
   policy only to record that transition: set `policy_activated: true`,
   `non_activation_reason: null`, `turn_status: "ACTION_REJECTED"`,
   `proposed_action: null`, and `user_evidence_recorded: null`. Preserve the
   existing action's `action_id`, `action_text`, `action_source`, and declared
   grounded facts in the semantic decision; do not erase or replace the rejected
   record and do not propose another action.
2. On a global STOP with an existing POL-03 proposal, use the same preservation
   rule and `turn_status: "STOPPED"`. If there is no existing proposal to close,
   do not activate: use `non_activation_reason: "user_refused_or_stopped"` and
   `turn_status: "NOT_APPLICABLE"`.
3. REVOCATION is not a global STOP and does not activate POL-03. Even when the prior action record
   is retained for provenance, strictly set `policy_activated: false`,
   `non_activation_reason: "user_refused_or_stopped"`, `turn_status: "NOT_APPLICABLE"`,
   `clarification_requested: false`, and `proposed_action: null`. Never use `STOPPED` for a
   REVOCATION turn.
4. Even on STOP, refusal, revocation, or any other non-activation path, emit at
   least one `decision_trace` event with `source: "POL-03"` explaining why no
   action is proposed. Never leave the trace empty. Use the following exact
   pattern when appropriate: on STOP or refusal,
   `[{"source":"POL-03","event":"STOP_NON_ACTIVATION","details":"No action is proposed because support was stopped or refused."}]`;
   on REVOCATION,
   `[{"source":"POL-03","event":"REVOCATION_NON_ACTIVATION","details":"No action is proposed because the prior support commitment was revoked."}]`.

## 5. Forbidden actions
1. Formulating if-then plans or generating situational cues, which belong exclusively to POL-01 (CRITICAL).
2. Proposing more than one action at a time (CRITICAL).
3. Handing off to POL-01 before `ACTION_ACCEPTED` (CRITICAL).
4. Inventing facts, materials, chapters or tasks not declared by the user (CRITICAL).
