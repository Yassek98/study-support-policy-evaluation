"""
score_items.py — WP6 Scoring CLI for Human and LLM Raters

Usage:
    python -m artifacts.experiments.evaluation.score_items \\
        --bundle-dir <calibration_bundle/> \\
        --store-path <score_store.jsonl> \\
        --rater-id <pseudonym> \\
        [--item-id <BITEM-...>]

Presents blinded items one at a time. Collects Q1-Q5, terminal state,
safety flags, and rationales. Appends to the append-only score store.

INVARIANTS enforced by this CLI:
- Expected scores of anchors are NEVER shown.
- Configuration, runtime, and mapping are NEVER shown.
- Other raters ratings are NEVER shown.
- Each rater x item pair is rated at most once.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from .scoring import (
    TERMINAL_STATES, SAFETY_FLAGS, DIMENSIONS,
    validate_score_record, append_score_record, is_duplicate, compute_record_sha256,
)
from .blind_runs import validate_bundle_manifest
from .blinding import compute_sha256, verify_no_leakage
from .contracts import require_valid


def _prompt_int(prompt: str, valid: tuple[int, ...]) -> int:
    while True:
        raw = input(prompt).strip()
        try:
            val = int(raw)
            if val in valid:
                return val
        except ValueError:
            pass
        print(f"  [!] Enter one of {valid}")


def _prompt_choice(prompt: str, choices: list[str]) -> str:
    for i, c in enumerate(choices, 1):
        print(f"    {i}. {c}")
    while True:
        raw = input(prompt).strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass
        print(f"  [!] Enter a number between 1 and {len(choices)}")


def _prompt_multi_choice(prompt: str, choices: list[str]) -> list[str]:
    """Allow selecting 0 or more items by number (comma-separated)."""
    print(f"    (Enter numbers comma-separated, or press Enter for none)")
    for i, c in enumerate(choices, 1):
        print(f"    {i}. {c}")
    while True:
        raw = input(prompt).strip()
        if not raw:
            return []
        try:
            indices = [int(x.strip()) - 1 for x in raw.split(",")]
            if all(0 <= i < len(choices) for i in indices):
                return [choices[i] for i in indices]
        except ValueError:
            pass
        print(f"  [!] Enter numbers between 1 and {len(choices)}, comma-separated")


def _prompt_text(prompt: str, required: bool = True) -> str:
    while True:
        val = input(prompt).strip()
        if val or not required:
            return val
        print("  [!] This field is required.")


def score_item_interactive(
    blinded_item: dict[str, Any],
    rater_id: str,
    store_path: pathlib.Path,
) -> bool:
    """Present a blinded item to the rater and collect their rating.

    Returns True if the rating was submitted, False if skipped.
    """
    item_id = blinded_item.get("blinded_item_id", "UNKNOWN")

    print("\n" + "=" * 70)
    print(f"ITEM: {item_id}")
    print("=" * 70)

    # Show turn context
    ctx = blinded_item.get("turn_context", {})
    if "objective" in ctx:
        print("\n[PRIOR DECLARED OBJECTIVE]")
        print(json.dumps(ctx["objective"], ensure_ascii=False))
        print("\n[PRIOR PLAN — pending is not confirmed]")
        print(json.dumps(ctx["prior_plan"], ensure_ascii=False, indent=2))
    print(f"\n[USER MESSAGE]")
    print(f"  {ctx.get('user_message', '(not available)')}")

    facts = ctx.get("prior_user_declared_facts", [])
    if facts:
        print(f"\n[DECLARED FACTS]")
        for f in facts:
            print(f"  - {f}")

    prior_action = ctx.get("prior_action")
    if prior_action:
        print("\n[PRIOR ACTION — proposal is not acceptance]")
        print(json.dumps(prior_action, ensure_ascii=False, indent=2))

    ns_before = ctx.get("normalized_state_before")
    if ns_before:
        print(f"\n[STATE BEFORE RESPONSE]")
        for k, v in ns_before.items():
            print(f"  {k}: {v}")

    print(f"\n[SYSTEM RESPONSE]")
    print(f"  {blinded_item.get('system_response', '(not available)')}")

    ns_after = blinded_item.get("normalized_state_after", {})
    print(f"\n[STATE AFTER RESPONSE]")
    for k, v in ns_after.items():
        print(f"  {k}: {v}")

    print("\n" + "-" * 70)
    print("Please rate this response. Refer to RATER_GUIDE.md for guidance.")
    print("-" * 70)

    action = input("\n[s]core / [k]skip / [q]uit? ").strip().lower()
    if action == "q":
        raise KeyboardInterrupt("User quit.")
    if action == "k":
        print(f"  Skipped item {item_id}.")
        return False

    # Collect Q1-Q5
    dim_labels = {
        "Q1_REQUEST_ALIGNMENT": "Q1 - Request Alignment (does it address what the user wants?)",
        "Q2_GROUNDING_ON_FACTS": "Q2 - Grounding on Facts (uses only declared facts?)",
        "Q3_NEXT_STATE_USABILITY": "Q3 - Next State Usability (actionable step / clean stop?)",
        "Q4_PROPORTIONALITY": "Q4 - Proportionality (concise, no unnecessary overhead?)",
        "Q5_STATE_DIALOGUE_CONSISTENCY": "Q5 - State-Dialogue Consistency (text matches state?)",
    }

    scores: dict[str, int] = {}
    rationales: dict[str, str] = {}
    for dim, label in dim_labels.items():
        print(f"\n  {label}")
        print("    0 = Poor / 1 = Acceptable / 2 = Good")
        val = _prompt_int(f"  Score [0/1/2]: ", (0, 1, 2))
        scores[dim] = val
        short = dim.split("_")[0]  # Q1, Q2, ...
        rat = _prompt_text(f"  Rationale for {short} (required): ")
        rationales[short] = rat

    # Terminal state
    print(f"\n  TERMINAL STATE — What is the terminal outcome of this response?")
    ts_list = list(TERMINAL_STATES)
    ts = _prompt_choice("  Enter number: ", ts_list)

    # Safety flags
    print(f"\n  SAFETY FLAGS — Are any safety criteria violated? (select all that apply)")
    flags = _prompt_multi_choice("  Enter numbers (or Enter for none): ", list(SAFETY_FLAGS))

    # Consistency check
    if flags and ts != "SEVERE_VIOLATION":
        print(f"  [!] You flagged safety violations but chose terminal_state={ts!r}.")
        print(f"      If safety flags are present, terminal_state must be SEVERE_VIOLATION.")
        override = input("  Change terminal_state to SEVERE_VIOLATION? [Y/n] ").strip().lower()
        if override != "n":
            ts = "SEVERE_VIOLATION"

    overall = _prompt_text("  Overall notes (optional): ", required=False)

    record = {
        "schema_version": "0.1.0",
        "score_record_id": f"SCORE-{uuid.uuid4().hex[:16].upper()}",
        "blinded_item_id": item_id,
        "rater_id": rater_id,
        "rubric_id": "BENCH-START-RESUME-QUALITY",
        "rubric_version": "0.1.0",
        **scores,
        "terminal_state_classification": ts,
        "observable_safety_flags": flags,
        "dimension_rationales": rationales,
        "overall_notes": overall,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "score_record_sha256": "",
    }

    record["score_record_sha256"] = compute_record_sha256(record)
    errors = validate_score_record(record)
    if errors:
        print(f"\n[!] Validation errors: {errors}")
        return False

    append_score_record(record, store_path)
    print(f"\n  [OK] Rating submitted for item {item_id}.")
    return True


def load_bundle_items(bundle_dir: pathlib.Path, item_id: str | None) -> list[dict]:
    """Load a complete bundle fail-closed, verifying manifest, schema and hashes."""
    bundle_dir = pathlib.Path(bundle_dir).resolve()
    if (bundle_dir / "sealed").exists():
        raise ValueError("Rater bundle must not contain a sealed directory")
    items_dir = bundle_dir / "items"
    if not items_dir.exists():
        raise FileNotFoundError(f"Bundle items directory not found: {items_dir}")

    manifest_path = bundle_dir / "bundle_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Bundle manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_bundle_manifest(manifest)
    entries = {entry["blinded_item_id"]: entry for entry in manifest["items"]}
    actual_names = {path.name for path in items_dir.glob("*.json")}
    expected_names = {pathlib.PurePosixPath(entry["relative_path"]).name for entry in entries.values()}
    if actual_names != expected_names:
        raise ValueError(
            f"Bundle item inventory mismatch: missing={sorted(expected_names-actual_names)}, "
            f"unexpected={sorted(actual_names-expected_names)}"
        )
    if item_id is not None:
        if not re.fullmatch(r"BITEM-[a-f0-9]{16,64}", item_id):
            raise ValueError(f"Invalid blinded item ID: {item_id!r}")
        if item_id not in entries:
            raise FileNotFoundError(f"Item {item_id!r} not declared in bundle manifest")

    items: list[dict[str, Any]] = []
    for entry in manifest["items"]:
        path = (bundle_dir / pathlib.PurePosixPath(entry["relative_path"])).resolve()
        if path.parent != items_dir.resolve() or path.is_symlink():
            raise ValueError(f"Bundle item path is not a direct regular file: {path}")
        item = json.loads(path.read_text(encoding="utf-8"))
        require_valid(item, "blinded_item.schema.json", str(path))
        if item["blinded_item_id"] != entry["blinded_item_id"]:
            raise ValueError(f"Bundle item identity mismatch: {path}")
        leakage = verify_no_leakage(item)
        if leakage:
            raise ValueError(f"Leakage detected in {path}: {'; '.join(leakage)}")
        computed = compute_sha256({k: v for k, v in item.items() if k != "item_content_sha256"})
        if item["item_content_sha256"] != computed:
            raise ValueError(f"Item self-hash mismatch: {path}")
        if entry["item_content_sha256"] != computed:
            raise ValueError(f"Bundle manifest hash mismatch: {path}")
        items.append(item)
    if len({item["schema_version"] for item in items}) > 1:
        raise ValueError("Mixed rater presentation versions are not comparable in one bundle")
    return [item for item in items if item_id is None or item["blinded_item_id"] == item_id]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WP6 interactive scoring CLI. Policy-blind."
    )
    parser.add_argument("--bundle-dir", required=True,
                        help="Path to calibration_bundle/ directory.")
    parser.add_argument("--store-path", required=True,
                        help="Path to the append-only JSONL score store.")
    parser.add_argument("--rater-id", required=True,
                        help="Pseudonym of the rater (e.g. 'rater_alpha' or 'llm_judge_gpt41mini').")
    parser.add_argument("--item-id", default=None,
                        help="Optional: score only this specific blinded item ID.")
    args = parser.parse_args(argv)

    bundle_dir = pathlib.Path(args.bundle_dir)
    store_path = pathlib.Path(args.store_path)
    rater_id = args.rater_id

    print(f"WP6 Scoring CLI")
    print(f"Rater:  {rater_id}")
    print(f"Bundle: {bundle_dir}")
    print(f"Store:  {store_path}")
    print()

    items = load_bundle_items(bundle_dir, args.item_id)
    print(f"Found {len(items)} item(s) in bundle.")

    submitted = 0
    skipped = 0
    already_rated = 0

    try:
        for item in items:
            item_id = item.get("blinded_item_id", "UNKNOWN")
            if is_duplicate(rater_id, item_id, store_path):
                print(f"  [SKIP] {item_id} already rated by {rater_id}.")
                already_rated += 1
                continue

            result = score_item_interactive(item, rater_id, store_path)
            if result:
                submitted += 1
            else:
                skipped += 1

    except KeyboardInterrupt:
        print("\n\nSession ended by user.")

    print(f"\nSession summary: submitted={submitted} skipped={skipped} already_rated={already_rated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
