"""Single-attempt shared-rubric execution with injected transport.

No CLI, credential discovery, automatic retries or default live transport. The
caller owns bundle verification, authorization and the cumulative budget ledger.
An existing attempt directory is never reused, including after interruption.
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from ..execution.live_adapter import HttpTransport, TimeoutException, TransportException
from .blinding import compute_sha256
from .measurement_contract import (
    build_shared_judge_messages, build_shared_score_record, judge_response_schema,
    judge_wire_response_schema, load_rubric, parse_shared_judge_wire_response,
)


@dataclass(frozen=True)
class JudgeSettings:
    model: str
    provider_route: str
    provider_response_name: str
    max_tokens: int = 2048
    seed: int = 42
    timeout_seconds: int = 30
    temperature: float | None = 0

    def validate(self):
        for value in (self.model, self.provider_route, self.provider_response_name):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Explicit model and route identities are required")
        for value in (self.max_tokens, self.timeout_seconds):
            if type(value) is not int or value <= 0:
                raise ValueError("Positive integer limits are required")
        if type(self.seed) is not int:
            raise ValueError("Seed must be an integer")
        if self.temperature is not None and (
            type(self.temperature) not in (int, float) or not 0 <= self.temperature <= 2
        ):
            raise ValueError("Temperature must be numeric in [0, 2] or omitted")


def build_request(item, settings: JudgeSettings):
    settings.validate()
    request = {
        "model": settings.model, "messages": build_shared_judge_messages(item),
        "max_tokens": settings.max_tokens, "seed": settings.seed,
        "reasoning": {"enabled": True, "effort": "low"}, "stream": False,
        "provider": {"only": [settings.provider_route], "allow_fallbacks": False,
                     "require_parameters": True, "data_collection": "deny"},
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "shared_quality_judgment", "strict": True,
            "schema": judge_wire_response_schema(),
        }},
    }
    if settings.temperature is not None:
        request["temperature"] = settings.temperature
    return request


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Invalid monetary value")
    try:
        amount = Decimal(str(value))
    except ArithmeticError as exc:
        raise ValueError("Invalid monetary value") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Finite nonnegative monetary value required")
    return amount


def _json_strict(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate envelope key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("Nonfinite envelope value")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def _save(path, value):
    # Exclusive creation is deliberate: incomplete artifacts block reuse too.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())


def execute_judge(*, item, settings: JudgeSettings, transport: HttpTransport,
                  api_key: str, attempt_dir: Path, rater_id: str,
                  available_usd, input_usd_per_million, output_usd_per_million,
                  max_request_bytes: int = 32768):
    """Persist intent before one dispatch, then raw reply and parsed disposition.

The bytes+1024 reservation is an exposure heuristic, NOT a provider billing cap.
Unknown actual cost and any unsuccessful outcome require stopping the batch.
Persistence failures propagate; callers must not retry the same observation.
Transport exceptions are classified without retaining potentially secret messages.
The injected transport must separately enforce redirect/response-size protections
before live use; this function is not a reviewed production network launcher.
"""
    if not isinstance(api_key, str) or not api_key or any(c.isspace() for c in api_key):
        raise ValueError("An explicit nonblank credential is required")
    if type(max_request_bytes) is not int or max_request_bytes <= 0:
        raise ValueError("Positive request byte limit required")
    request = build_request(item, settings)
    body = json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if api_key.encode() in body:
        raise ValueError("Credential appears in request content")
    # Validate the runner-owned identity before any billable action.
    placeholder = {key: {"score": None, "evidence": "Preflight only"}
                   for key in judge_response_schema()["required"]}
    build_shared_score_record(item=item, ratings=placeholder, rater_id=rater_id,
                              rater_role="LLM_JUDGE")
    available = _money(available_usd)
    reserved = ((len(body) + 1024) * _money(input_usd_per_million)
                + settings.max_tokens * _money(output_usd_per_million)) / 1_000_000
    if len(body) > max_request_bytes or reserved > available:
        raise ValueError("Request exceeds declared byte or reserved-cost allowance")
    receipt = {
        "record_type": "SHARED_JUDGE_ATTEMPT", "version": "0.1.0",
        "attempt_id": "JUDGE-" + uuid4().hex.upper(),
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "blinded_item_id": item["blinded_item_id"],
        "item_content_sha256": item["item_content_sha256"],
        "rubric_sha256": compute_sha256(load_rubric()), "settings": asdict(settings),
        "request_sha256": compute_sha256(request), "rater_id": rater_id,
        "reserved_usd": str(reserved), "reservation_is_billing_cap": False,
        "actual_cost_usd": None, "status": "PREPARED", "stop_required": True,
    }
    if api_key in json.dumps(receipt):
        raise ValueError("Credential appears in attempt metadata")
    directory = Path(attempt_dir)
    directory.mkdir(parents=False, exist_ok=False)
    _save(directory / "request.json", request)
    _save(directory / "prepared.json", receipt)
    started = time.perf_counter()
    try:
        response = transport.request(
            "POST", "https://openrouter.ai/api/v1/chat/completions",
            {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            body, settings.timeout_seconds,
        )
    except (TimeoutException, TransportException) as exc:
        receipt['latency_ms'] = (time.perf_counter() - started) * 1000
        receipt["status"] = "TIMEOUT" if isinstance(exc, TimeoutException) else "TRANSPORT_ERROR"
        _save(directory / "result.json", receipt)
        return receipt
    except Exception:
        # The transport may have dispatched before failing unexpectedly. Retain
        # an unknown-cost failure rather than authorizing another attempt.
        receipt["status"] = "UNEXPECTED_TRANSPORT_ERROR"
        receipt['latency_ms'] = (time.perf_counter() - started) * 1000
        _save(directory / "result.json", receipt)
        return receipt

    receipt['latency_ms'] = (time.perf_counter() - started) * 1000
    # Preserve bytes even if decoding fails; never persist the supplied credential.
    redacted = response.body.replace(api_key.encode(), b"[REDACTED_CREDENTIAL]")
    response_record = {
        "http_status": response.status_code,
        "body_base64": base64.b64encode(redacted).decode("ascii"),
        "credential_redacted": redacted != response.body,
    }
    _save(directory / "response.json", response_record)
    receipt["response_sha256"] = compute_sha256(response_record)
    receipt["status"] = "INVALID_ENVELOPE"
    envelope = None
    try:
        envelope = _json_strict(redacted.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        pass
    if isinstance(envelope, dict):
        usage = envelope.get("usage")
        if isinstance(usage, dict) and "cost" in usage:
            try:
                receipt["actual_cost_usd"] = str(_money(usage["cost"]))
            except ValueError:
                pass
        if redacted != response.body:
            receipt["status"] = "CREDENTIAL_ECHO"
        elif response.status_code != 200 or "error" in envelope:
            receipt["status"] = "PROVIDER_ERROR"
        elif (envelope.get("model") != settings.model
              or envelope.get("provider") != settings.provider_response_name):
            receipt["status"] = "IDENTITY_MISMATCH"
        else:
            choices = envelope.get("choices")
            if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
                choice = choices[0]
                message = choice.get("message")
                if choice.get("finish_reason") != "stop":
                    receipt["status"] = "INCOMPLETE_RESPONSE"
                elif (isinstance(message, dict) and not message.get("refusal")
                      and not message.get("tool_calls") and isinstance(message.get("content"), str)):
                    try:
                        ratings = parse_shared_judge_wire_response(message["content"])
                    except (ValueError, RecursionError):
                        receipt["status"] = "INVALID_JUDGMENT"
                    else:
                        score = build_shared_score_record(item=item, ratings=ratings,
                            rater_id=rater_id, rater_role="LLM_JUDGE")
                        _save(directory / "score.json", score)
                        receipt["score_record_sha256"] = score["score_record_sha256"]
                        receipt["status"] = "PARSED"
    elif response.status_code != 200:
        receipt["status"] = "PROVIDER_ERROR"
    cost = receipt["actual_cost_usd"]
    receipt["stop_required"] = (receipt["status"] != "PARSED" or cost is None
                                 or _money(cost) > reserved)
    _save(directory / "result.json", receipt)
    return receipt
