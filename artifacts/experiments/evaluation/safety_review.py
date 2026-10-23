"""Separate, source-bound prose-safety review, including readable invalid outputs.

Never infer reviewed safety from a model flag, lexical detector, or quality rating.
"""
import hashlib
import json

from ..execution.reconciliation import _confined
from .blinding import compute_sha256


def safety_inventory(*, results, output_root):
    """Prepare evidence, not human judgments. Caller supplies reconciled results."""
    from pathlib import Path
    root = Path(output_root).resolve()
    items, seen = [], set()
    for result in results:
        run_id = result["run_id"]
        if run_id in seen:
            raise ValueError("Duplicate safety-review invocation")
        seen.add(run_id)
        attempts = result["attempts"]
        if len(attempts) > 1:
            raise ValueError("Single-attempt safety inventory required")
        attempt = attempts[0] if attempts else {}
        path = attempt.get("model_output_path") or (
            attempt.get("raw_response_path") if attempt.get("adapter_status") == "SUCCESS" else None)
        fingerprint = (attempt.get("model_output_sha256") if attempt.get("model_output_path")
                       else attempt.get("raw_response_sha256"))
        text, response, source_hash = None, None, None
        if path is not None:
            raw = _confined(root, path).read_bytes()
            source_hash = hashlib.sha256(raw).hexdigest()
            if source_hash != fingerprint:
                raise ValueError("Safety source hash mismatch")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                pass
            if text:
                from ..execution.runner import reject_duplicate_json_keys, reject_nonstandard_json_constant
                try:
                    parsed = json.loads(text, object_pairs_hook=reject_duplicate_json_keys,
                                        parse_constant=reject_nonstandard_json_constant)
                    if isinstance(parsed, dict) and isinstance(parsed.get("system_response"), str):
                        response = parsed["system_response"]
                except ValueError:
                    pass
        item = {"run_id": run_id, "producer_outcome": result["final_outcome_code"],
                "source_path": path, "source_sha256": source_hash,
                "raw_model_text": text, "extracted_response": response,
                "availability": ("READABLE_PAYLOAD_REVIEW_REQUIRED" if text and text.strip()
                                 else "NO_READABLE_MODEL_PAYLOAD")}
        item["item_sha256"] = compute_sha256(item)
        items.append(item)
    return items


def make_safety_review(*, item, reviewer_id, reviewer_role, finding, evidence, violation_codes):
    """Record an explicitly supplied review; this function does not generate a judgment."""
    if item.get("item_sha256") != compute_sha256({k: v for k, v in item.items() if k != "item_sha256"}):
        raise ValueError("Safety item hash mismatch")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError("Reviewer identity required")
    if reviewer_role not in {"AUTHOR_ASSOCIATED", "EXTERNAL_REVIEWER"}:
        raise ValueError("Explicit reviewer role required")
    if finding not in {"NO_VIOLATION_OBSERVED", "VIOLATION_OBSERVED", "UNINSPECTABLE"}:
        raise ValueError("Unknown review disposition")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("Review evidence or uninspectability reason required")
    if (not isinstance(violation_codes, list)
            or any(not isinstance(code, str) or not code.strip() for code in violation_codes)
            or len(set(violation_codes)) != len(violation_codes)):
        raise ValueError("Unique nonblank contract violation identifiers required")
    if (finding == "VIOLATION_OBSERVED") != bool(violation_codes):
        raise ValueError("Violation codes and finding disagree")
    if item["availability"] == "NO_READABLE_MODEL_PAYLOAD" and finding != "UNINSPECTABLE":
        raise ValueError("Unreadable output cannot be labelled safe or violating")
    record = {"run_id": item["run_id"], "item_sha256": item["item_sha256"],
        "reviewer_id": reviewer_id, "reviewer_role": reviewer_role,
        "finding": finding, "evidence": evidence, "violation_codes": violation_codes}
    record["review_sha256"] = compute_sha256(record)
    return record


def summarize_safety_reviews(*, items, reviews):
    """Retain unreviewed and uninspectable populations; never merge into Q1–Q5."""
    inventory = {item["run_id"]: item for item in items}
    if len(inventory) != len(items):
        raise ValueError("Duplicate safety inventory identity")
    seen, rows, roles = set(), [], {}
    for review in reviews:
        pair = (review["run_id"], review["reviewer_id"])
        if pair in seen or review["run_id"] not in inventory:
            raise ValueError("Duplicate or unplanned safety review")
        seen.add(pair)
        if roles.setdefault(review["reviewer_id"], review["reviewer_role"]) != review["reviewer_role"]:
            raise ValueError("One reviewer identity cannot silently change safety-review role")
        expected = make_safety_review(item=inventory[review["run_id"]],
            **{key: review[key] for key in ("reviewer_id", "reviewer_role", "finding", "evidence", "violation_codes")})
        if review != expected:
            raise ValueError("Safety review binding or hash mismatch")
    for item in items:
        assigned = [review for review in reviews if review["run_id"] == item["run_id"]]
        rows.append({"run_id": item["run_id"], "producer_outcome": item["producer_outcome"],
                     "availability": item["availability"], "reviews": assigned,
                     "status": "REVIEWED" if assigned else "UNREVIEWED"})
    readable = [row for row in rows if row["availability"] == "READABLE_PAYLOAD_REVIEW_REQUIRED"]
    author_reviewed = sum(any(r["reviewer_role"] == "AUTHOR_ASSOCIATED" for r in row["reviews"])
                          for row in readable)
    return {"planned": len(items), "reviewed_invocations": sum(row["status"] == "REVIEWED" for row in rows),
            "readable_payloads": sum(item["availability"] == "READABLE_PAYLOAD_REVIEW_REQUIRED" for item in items),
            "readable_with_author_review": author_reviewed,
            "readable_missing_author_review": len(readable) - author_reviewed,
            "reviewer_roles": roles,
            "rows": rows, "scope": "Assigned prose reviews only; reviewer role is not proof of independence"}


def persist_safety_review(*, output_path, **review):
    """Retain one supplied review exclusively, without overwriting prior judgments."""
    from pathlib import Path
    from ..execution.planned_execution import _save_exclusive
    record = make_safety_review(**review)
    _save_exclusive(Path(output_path), record)
    return record
