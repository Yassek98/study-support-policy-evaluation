"""Loopback-only ordinary rating interface; it neither calls a model nor decides outcomes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
from pathlib import Path
import secrets
from uuid import uuid4

from .rating_assignments import validate_assignments
from .score_items import load_bundle_items
from .scoring import (DIMENSIONS, SAFETY_FLAGS, ScoreStoreIntegrityError,
                      append_score_record, compute_record_sha256, load_score_records,
                      validate_score_record)


PRESENTATION_VERSION = "ordinary-interface-0.2.3"
RUBRIC_VERSION = "0.2.1"
RATING_INSTRUCTIONS = (
    "Judge this response on its own, using only the context shown. Similar responses may receive the same scores.",
    "Score each criterion separately. Use 1 for a specific partial weakness, not simply because you feel uncertain.",
    "If essential context or an anchor is unclear, pause and ask the coordinator rather than inventing information. An optional comment can explain the difficulty.",
)
CRITERIA = (
    ("Q1_REQUEST_ALIGNMENT", "Relevance", "Does it address the user's current request?", "Misses or opposes the request", "Addresses only part of the request", "Directly addresses the request"),
    ("Q2_GROUNDING_ON_FACTS", "Grounding in facts", "Does it use the supplied facts without inventing personal circumstances or commitments?", "Invents a material fact or commitment", "Adds a minor unsupported assumption", "Uses supplied facts or asks when needed"),
    ("Q3_NEXT_STATE_USABILITY", "Usability", "Does it leave a usable step, a needed clarification, or a clean stop when requested?", "Leaves no usable way forward or ignores stop", "A way forward exists but is unclear", "Clear way forward or requested clean stop"),
    ("Q4_PROPORTIONALITY", "Proportionality and readability", "Is the amount and presentation appropriate to this request, rather than just short?", "Excess or presentation obstructs reading", "Readable, with avoidable overhead", "Easy to follow, with proportionate detail"),
    ("Q5_STATE_DIALOGUE_CONSISTENCY", "Conversation consistency", "Does it fit prior context, the current message and session status? A proposal alone is not acceptance.", "Contradicts context or invents acceptance", "Ambiguous continuity or acceptance", "Consistent with context, message and status"),
)


def _plain_context(item: dict) -> dict[str, str]:
    """Expose only human-readable context needed for this item, without state codes."""
    turn = item["turn_context"]
    prior: list[str] = []
    if turn.get("objective"):
        prior.append(turn["objective"])
    prior.extend(turn.get("prior_user_declared_facts", []))
    action = turn.get("prior_action")
    if action:
        labels = {"ACTION_PROPOSED": "proposed, not yet accepted",
                  "ACTION_ACCEPTED": "accepted", "ACTION_REJECTED": "rejected",
                  "CLARIFICATION_PENDING": "awaiting clarification", "UNKNOWN": "acceptance unknown"}
        prior.append(f"Earlier action ({labels.get(action['status'], 'status unknown')}): {action['text']}")
        if action.get("acceptance_evidence"):
            prior.append("Recorded acceptance evidence: " + action["acceptance_evidence"])
    plan = turn.get("prior_plan")
    if plan:
        prior.append("Earlier plan — cue: " + (plan.get("cue") or "not supplied")
                     + "; action: " + (plan.get("action") or "not supplied")
                     + "; recorded status: " + (plan.get("status") or "unknown"))
    controls = {"ACTIVE": "active", "STOPPED": "stopped", "REFUSED": "refused",
                "REVOKED": "permission revoked", "MODIFICATION_REQUESTED": "modification requested"}
    permissions = {"ALLOWED": "allowed", "BLOCKED": "blocked"}
    status = []
    for label, state in (("Before the response", turn.get("normalized_state_before", {})),
                         ("After the response (system record)", item.get("normalized_state_after", {}))):
        status.append(f"{label}: session {controls.get(state.get('session_control'), 'unknown')}; "
                      f"support {permissions.get(state.get('support_permission'), 'unknown')}.")
    return {
        "prior_summary": "\n".join(prior) if prior else "No prior context was supplied.",
        "user_message": turn["user_message"],
        "response": item["system_response"],
        "conversation_status": " ".join(status),
    }


class RatingSession:
    """Retained rubric-0.2.1 session for historical development compatibility."""
    presentation_version = PRESENTATION_VERSION
    rubric_version = RUBRIC_VERSION
    record_version = "0.2.0"
    instructions = RATING_INSTRUCTIONS
    criteria = CRITERIA
    html_filename = "rating_app.html"

    def _validate_items(self, items):
        pass  # load_bundle_items already validates the legacy presentation contract.

    def __init__(self, bundle_dir, assignments, rater_id, store_path):
        if self.record_version == "0.2.0":
            self.instructions, self.criteria = RATING_INSTRUCTIONS, CRITERIA
            self.rubric_version, self.presentation_version = RUBRIC_VERSION, PRESENTATION_VERSION
        items = load_bundle_items(Path(bundle_dir), None)
        self._validate_items(items)
        if any(item["schema_version"] != "0.3.0" for item in items):
            raise ValueError("The browser interface requires presentation 0.3.0")
        validate_assignments(assignments, [item["blinded_item_id"] for item in items])
        if rater_id not in assignments:
            raise ValueError("Rater is not assigned")
        by_id = {item["blinded_item_id"]: item for item in items}
        self.items = [by_id[item_id] for item_id in assignments[rater_id]]
        self.item_ids, self.rater_id = set(assignments[rater_id]), rater_id
        self.store_path, self.token = Path(store_path), secrets.token_urlsafe(32)
        binding = {"presentation": self.presentation_version, "rubric_version": self.rubric_version,
                   "instructions": self.instructions, "criteria": self.criteria,
                   "rendered_items": [_plain_context(item) for item in self.items],
                   "rater_id": rater_id, "assigned_items": self.items,
                   "assignments": assignments}
        if self.record_version == "0.3.0":
            binding["rater_role"] = self.rater_role
            binding["rubric_sha256"] = self.rubric_sha256
        digest = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar = self.store_path.with_name(self.store_path.name + ".binding.json")
        if sidecar.exists():
            if json.loads(sidecar.read_text(encoding="utf-8")) != {"binding_sha256": digest}:
                raise ValueError("Store bound to different items, presentation, or assignment")
        elif self.store_path.exists():
            raise ValueError("Existing store has no binding; do not adopt it implicitly")
        else:
            with sidecar.open("x", encoding="utf-8") as handle:
                json.dump({"binding_sha256": digest}, handle)
        self._records()

    def _records(self):
        records = load_score_records(self.store_path)
        if any(record["schema_version"] != self.record_version or record["rubric_version"] != self.rubric_version or record["rater_id"] != self.rater_id or record["blinded_item_id"] not in self.item_ids
               for record in records):
            raise ValueError("Use a separate, assignment-compatible store for this rater")
        return records

    def state(self):
        done = {record["blinded_item_id"] for record in self._records()}
        item = next((item for item in self.items if item["blinded_item_id"] not in done), None)
        return {"rater": self.rater_id, "submitted": len(done), "assigned": len(self.items),
                "instructions": RATING_INSTRUCTIONS,
                "item": _plain_context(item) | {"blinded_item_id": item["blinded_item_id"]} if item else None,
                "criteria": [{"id": c[0], "title": c[1], "question": c[2], "anchors": c[3:]}
                             for c in CRITERIA], "safety_flags": list(SAFETY_FLAGS)}

    def submit(self, payload):
        expected = {"blinded_item_id", "scores", "safety_review_signals", "comment"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("Unexpected rating fields")
        current = self.state()["item"]
        if current is None or payload["blinded_item_id"] != current["blinded_item_id"]:
            raise ValueError("Item is already submitted, unassigned, or not current")
        if not isinstance(payload["scores"], dict) or set(payload["scores"]) != set(DIMENSIONS):
            raise ValueError("Five scores are required")
        if not isinstance(payload["safety_review_signals"], list) or any(
                signal not in SAFETY_FLAGS for signal in payload["safety_review_signals"]):
            raise ValueError("Invalid safety-review signal")
        if not isinstance(payload["comment"], str) or len(payload["comment"]) > 4000:
            raise ValueError("Invalid optional comment")
        record = {"schema_version": "0.2.0", "score_record_id": "SCORE-" + uuid4().hex.upper(),
                  "blinded_item_id": current["blinded_item_id"], "rater_id": self.rater_id,
                  "rubric_id": "BENCH-START-RESUME-QUALITY", "rubric_version": RUBRIC_VERSION,
                  "ordinary_interface_scope": "FIVE_DIMENSIONS_ONLY_OUTCOME_NOT_COLLECTED",
                  **payload["scores"], "safety_review_signals": payload["safety_review_signals"],
                  "submitted_at": datetime.now(timezone.utc).isoformat()}
        if payload["comment"].strip():
            record["comment"] = payload["comment"].strip()
        record["score_record_sha256"] = compute_record_sha256(record)
        errors = validate_score_record(record)
        if errors:
            raise ValueError("; ".join(errors))
        append_score_record(record, self.store_path)


class SharedRatingSession(RatingSession):
    """Shared English instrument; fresh bound stores only, never historical relabelling."""
    presentation_version = "ordinary-interface-0.3.0"
    rubric_version = "0.3.0"
    record_version = "0.3.0"
    html_filename = "rating_shared.html"

    def __init__(self, bundle_dir, assignments, rater_id, store_path, *, rater_role):
        from .measurement_contract import load_rubric
        from .blinding import compute_sha256
        if rater_role not in {"AUTHOR_ASSOCIATED", "EXTERNAL_HUMAN"}:
            raise ValueError("A human reviewer role must be assigned explicitly")
        self.rater_role = rater_role
        self.rubric = load_rubric()
        self.rubric_sha256 = compute_sha256(self.rubric)
        self.instructions = self.rubric["instructions"]
        self.criteria = self.rubric["criteria"]
        super().__init__(bundle_dir, assignments, rater_id, store_path)

    def _validate_items(self, items):
        from .measurement_contract import shared_interaction_payload
        for item in items:
            shared_interaction_payload(item)

    def _records(self):
        records = super()._records()
        hashes = {item["blinded_item_id"]: item["item_content_sha256"] for item in self.items}
        if any(record["rater_role"] != self.rater_role
               or record["rubric_sha256"] != self.rubric_sha256
               or record["item_content_sha256"] != hashes[record["blinded_item_id"]]
               for record in records):
            raise ValueError("Shared scores do not match the assigned role/item/rubric")
        return records

    def state(self):
        done = {record["blinded_item_id"] for record in self._records()}
        item = next((item for item in self.items if item["blinded_item_id"] not in done), None)
        return {"rater": self.rater_id, "submitted": len(done), "assigned": len(self.items),
                "rubric_version": self.rubric_version, "instructions": self.instructions,
                "criteria": self.criteria,
                "item": _plain_context(item) | {"blinded_item_id": item["blinded_item_id"]} if item else None}

    def submit(self, payload):
        from .measurement_contract import build_shared_score_record
        if type(payload) is not dict or set(payload) != {"blinded_item_id", "ratings", "comment"}:
            raise ValueError("Unexpected shared rating fields")
        current = self.state()["item"]
        if current is None or payload["blinded_item_id"] != current["blinded_item_id"]:
            raise ValueError("Item is not the current assigned item")
        item = next(item for item in self.items if item["blinded_item_id"] == current["blinded_item_id"])
        record = build_shared_score_record(item=item, ratings=payload["ratings"],
            rater_id=self.rater_id, rater_role=self.rater_role, comment=payload["comment"])
        append_score_record(record, self.store_path)


def make_server(session, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def send_payload(self, status, data, content_type="application/json"):
            body = data.encode("utf-8") if isinstance(data, str) else json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers(); self.wfile.write(body)
        def host_valid(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
        def do_GET(self):
            if not self.host_valid(): return self.send_payload(403, {"error": "Host rejected"})
            if self.path == "/":
                html = Path(__file__).with_name(session.html_filename).read_text(encoding="utf-8")
                return self.send_payload(200, html.replace("__SESSION_TOKEN__", session.token), "text/html")
            if self.path == "/api/state":
                try: return self.send_payload(200, session.state())
                except (OSError, ValueError, ScoreStoreIntegrityError): return self.send_payload(500, {"error": "Store unavailable or inconsistent. Contact the coordinator."})
            return self.send_payload(404, {"error": "Not found"})
        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self.host_valid() or self.headers.get("Origin") != origin or not secrets.compare_digest(self.headers.get("X-Session-Token", ""), session.token):
                return self.send_payload(403, {"error": "Request rejected"})
            if self.path != "/api/ratings": return self.send_payload(404, {"error": "Not found"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 32768 or self.headers.get("Content-Type") != "application/json": raise ValueError("Invalid request")
                session.submit(json.loads(self.rfile.read(size)))
                self.send_payload(200, {"saved": True})
            except (ValueError, TypeError, KeyError, UnicodeError):
                self.send_payload(400, {"error": "Rating not saved. Check the five scores and try again."})
            except (OSError, ScoreStoreIntegrityError):
                self.send_payload(500, {"error": "Storage error. Do not close the page; contact the coordinator."})
    return HTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", required=True); parser.add_argument("--assignments", required=True)
    parser.add_argument("--rater-id", required=True); parser.add_argument("--store-path", required=True)
    parser.add_argument("--port", type=int, default=0, help="Optional port on 127.0.0.1 (defaults to ephemeral)")
    parser.add_argument("--legacy-rubric", action="store_true", help="Explicitly use historical rubric 0.2.1")
    parser.add_argument("--rater-role", choices=["AUTHOR_ASSOCIATED", "EXTERNAL_HUMAN"])
    args = parser.parse_args()
    if not args.legacy_rubric and args.rater_role is None:
        parser.error("Shared rubric requires --rater-role; use a fresh score store")
    session_type = RatingSession if args.legacy_rubric else SharedRatingSession
    role_args = {} if args.legacy_rubric else {"rater_role": args.rater_role}
    session = session_type(args.bundle_dir, json.loads(Path(args.assignments).read_text(encoding="utf-8")), args.rater_id, args.store_path, **role_args)
    server = make_server(session, port=args.port); print(f"Local rating page: http://127.0.0.1:{server.server_port}", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__":
    main()
