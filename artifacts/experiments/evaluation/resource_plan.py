"""Finite-design workload accounting, not a bill, balance or live-call grant."""
from decimal import Decimal
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


def planned_workload(*, families=10, configurations=8, replicas=2, scenarios=74,
                     criteria=5, categories=3, challenge_realizations=2,
                     correction_cycles=0):
    values = (families, configurations, replicas, scenarios, criteria, categories,
              challenge_realizations)
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("Design counts must be positive integers")
    if type(correction_cycles) is not int or correction_cycles not in (0, 1):
        raise ValueError("At most one predeclared correction cycle")
    training = criteria * categories
    natural = families * configurations
    challenges = training * challenge_realizations
    cycles = 1 + correction_cycles
    development_producer = families * configurations * replicas * cycles
    development_judge = (natural + challenges) * cycles
    final_producer = scenarios * configurations * replicas
    return {
        "development_producer_slots": development_producer,
        "development_judge_slots": development_judge,
        "final_producer_slots": final_producer,
        "final_judge_slots_ceiling": final_producer,
        "total_call_slots_ceiling": development_producer + development_judge + 2 * final_producer,
        "training_examples_initial": training,
        "natural_items_per_rater": natural * cycles,
        "challenge_items_per_rater": challenges * cycles,
        "criterion_ratings_per_rater": (natural + challenges) * criteria * cycles,
        "independent_final_scenario_reviews": scenarios,
        "extra_work_not_in_counts": [
            "Development scenario and anchor authoring/review",
            "Training discussion, unresolved-item repair and adjudication",
            "Any replacement training after instrument correction",
            "Separate safety review and disciplinary-assumption review",
        ],
        "scope": "Planned slots; failed production does not fabricate a judge item",
    }


def sensitivity_cost(workload, *, producer_unit_usd, judge_unit_usd):
    """Apply declared hypothetical unit costs; no hidden FX or zero unknowns."""
    rates = []
    for raw in (producer_unit_usd, judge_unit_usd):
        if isinstance(raw, bool) or raw is None:
            raise ValueError("Explicit finite unit costs required")
        try:
            rate = Decimal(str(raw))
        except ArithmeticError as exc:
            raise ValueError("Invalid unit cost") from exc
        if not rate.is_finite() or rate < 0:
            raise ValueError("Explicit finite nonnegative unit costs required")
        rates.append(rate)
    producer, judge = rates
    result = {
        "development_usd": workload["development_producer_slots"] * producer
                           + workload["development_judge_slots"] * judge,
        "final_usd": workload["final_producer_slots"] * producer
                     + workload["final_judge_slots_ceiling"] * judge,
    }
    result["total_usd"] = result["development_usd"] + result["final_usd"]
    return result


def observed_producer_resources(*, reconciliation, output_root):
    """Describe retained burden including failed calls; unknown receipts stay unknown.

    Requires the reconciled source ledger. Provider-reported cost is read from its
    hash-bound envelope, never estimated from token rates or inferred from success.
    """
    from ..execution.reconciliation import _confined
    from ..execution.runner import reject_duplicate_json_keys, reject_nonstandard_json_constant
    root = Path(output_root).resolve()
    by_id = {r['run_id']: r for r in reconciliation['results']}
    def read_bytes(attempt, prefix):
        path = attempt.get(prefix + '_path')
        if path is None:
            return None
        raw = _confined(root, path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != attempt.get(prefix + '_sha256'):
            raise ValueError('Resource evidence hash mismatch')
        return raw
    rows = []
    for slot in reconciliation['rows']:
        result = by_id.get(slot['run_id'])
        attempts = result['attempts'] if result is not None else []
        if len(attempts) > 1:
            raise ValueError('Selected accounting expects at most one attempt per slot')
        attempt = attempts[0] if attempts else {}
        request = read_bytes(attempt, 'request_body')
        envelope_raw = read_bytes(attempt, 'provider_envelope')
        usage = {}
        if envelope_raw is not None:
            try:
                envelope = json.loads(envelope_raw, object_pairs_hook=reject_duplicate_json_keys,
                                      parse_constant=reject_nonstandard_json_constant)
                if isinstance(envelope, dict) and isinstance(envelope.get('usage'), dict):
                    usage = envelope['usage']
            except (ValueError, UnicodeError):
                pass
        cost = None
        if usage.get('cost') is not None and not isinstance(usage['cost'], bool):
            try:
                amount = Decimal(str(usage['cost']))
                if amount.is_finite() and amount >= 0:
                    cost = str(amount)
            except ArithmeticError:
                pass
        tokens = attempt.get('token_usage') or {}
        latency = attempt.get('latency_ms')
        if type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0:
            latency = None
        rows.append({k: slot[k] for k in ('run_id', 'configuration_id', 'state') } | {
            'attempt_recorded': bool(attempts), 'outcome': slot['outcome'],
            'request_bytes': len(request) if request is not None else None,
            'latency_ms': latency, 'actual_cost_usd': cost,
            **{k: tokens.get(k) if type(tokens.get(k)) is int and tokens[k] >= 0 else None
               for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}})
    return {'overall': _summarize_resources(rows), 'by_configuration': {
        c: _summarize_resources([r for r in rows if r['configuration_id'] == c])
        for c in sorted({r['configuration_id'] for r in rows})}, 'rows': rows,
        'scope': 'Retained single-attempt telemetry; unknown cost/tokens are not zero; not an invoice'}


def _summarize_resources(rows):
    attempted = [r for r in rows if r['attempt_recorded']]
    fields = {}
    for key in ('request_bytes', 'latency_ms', 'prompt_tokens', 'completion_tokens', 'total_tokens', 'actual_cost_usd'):
        known = [r[key] for r in attempted if r[key] is not None]
        total = str(sum((Decimal(v) for v in known), Decimal(0))) if key == 'actual_cost_usd' else sum(known)
        fields[key] = {'known_attempts': len(known), 'unknown_attempts': len(attempted) - len(known),
                       'observed_sum': total if known else None,
                       'complete_sum': total if len(known) == len(attempted) else None}
    return {'planned': len(rows), 'attempt_records': len(attempted),
            'states': dict(Counter(r['state'] for r in rows)), 'metrics': fields}


def observed_judge_resources(*, reconciliation, session_dir):
    """Describe an already reconciled judge session, including retained failures."""
    import base64
    from ..execution.reconciliation import _read, _confined
    from .blinding import compute_sha256
    root = Path(session_dir).resolve()
    rows = []
    for index, source in enumerate(reconciliation['rows'], 1):
        directory = _confined(root, f'attempt-{index:04d}')
        request_bytes, usage = None, {}
        if (directory / 'request.json').exists():
            request = _read(directory / 'request.json')
            request_bytes = len(json.dumps(request, ensure_ascii=False, sort_keys=True).encode('utf-8'))
        receipt = _read(directory / 'result.json') if (directory / 'result.json').exists() else {}
        if (compute_sha256(receipt) if receipt else None) != source.get('result_sha256'):
            raise ValueError('Judge resource receipt changed after reconciliation')
        latency = receipt.get('latency_ms')
        if latency is not None and (type(latency) not in (int, float)
                or not math.isfinite(latency) or latency < 0):
            raise ValueError('Invalid retained judge latency')
        if (directory / 'response.json').exists():
            response = _read(directory / 'response.json')
            if receipt and receipt.get('response_sha256') != compute_sha256(response):
                raise ValueError('Judge resource response binding mismatch')
            try:
                envelope = json.loads(base64.b64decode(response['body_base64'], validate=True))
                if isinstance(envelope, dict) and isinstance(envelope.get('usage'), dict):
                    usage = envelope['usage']
            except (ValueError, UnicodeError):
                pass
        rows.append({'blinded_item_id': source['blinded_item_id'], 'state': source['state'],
            'attempt_recorded': source['state'] != 'NOT_STARTED', 'request_bytes': request_bytes,
            'latency_ms': latency, 'actual_cost_usd': source['actual_cost_usd'],
            **{k: usage.get(k) if type(usage.get(k)) is int and usage[k] >= 0 else None
               for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}})
    return {'overall': _summarize_resources(rows), 'rows': rows,
            'scope': 'Retained judge telemetry; unreported metrics remain unknown; not an invoice'}
