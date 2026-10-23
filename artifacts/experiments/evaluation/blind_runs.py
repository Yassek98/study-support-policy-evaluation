"""Build a transactional policy-label-blind bundle from canonical run pairs."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .blinding import build_blinded_item, compute_sha256, generate_blind_id, verify_no_leakage, write_atomic
from .contracts import require_valid
from .state_projection import project_state

try:
    from artifacts.experiments.execution.configuration_loader import load_validated_configuration
    from artifacts.experiments.execution.invocation_plan import verify_result_coverage
    from artifacts.experiments.execution.run_record_validator import validate_run_record_semantics
except ImportError:
    from execution.configuration_loader import load_validated_configuration
    from execution.invocation_plan import verify_result_coverage
    from execution.run_record_validator import validate_run_record_semantics


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_CALIBRATION_ROOT = REPO_ROOT / "artifacts" / "experiments" / "runs" / "calibration"
_BATCH_RE = re.compile(r"^BATCH-[A-Z0-9_-]+$")
_BLIND_ITEM_RE = re.compile(r"^BITEM-[a-f0-9]{16,64}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class SourcePair:
    record_path: pathlib.Path
    result_path: pathlib.Path


def _file_sha256(path: pathlib.Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_run_pairs(runs_dir: pathlib.Path) -> list[SourcePair]:
    """Discover canonical ``record.json`` + sibling ``result.json`` pairs."""
    runs_dir = runs_dir.resolve()
    if not runs_dir.is_dir():
        raise ValueError(f"Runs directory does not exist: {runs_dir}")
    pairs: list[SourcePair] = []
    for record_path in sorted(runs_dir.rglob("record.json")):
        result_path = record_path.with_name("result.json")
        if not result_path.is_file():
            raise ValueError(f"Missing sibling result.json for {record_path}")
        pairs.append(SourcePair(record_path, result_path))
    if not pairs:
        raise ValueError(f"No canonical record.json/result.json pairs found under {runs_dir}")
    return pairs


def discover_execution_results(runs_dir: pathlib.Path) -> list[dict[str, Any]]:
    """Load every canonical success or failure ledger under an execution root."""
    runs_dir = runs_dir.resolve()
    candidates = {
        path.resolve()
        for path in runs_dir.rglob("*.json")
        if path.name == "result.json" or path.parent.name == "results"
    }
    if not candidates:
        raise ValueError(f"No execution result ledgers found under {runs_dir}")
    results: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    for path in sorted(candidates):
        result = _load_json(path, "execution result")
        require_valid(result, "run_execution_result.schema.json", str(path))
        run_id = result.get("run_id")
        if run_id in seen_run_ids:
            raise ValueError(f"Duplicate execution result ledger for run_id: {run_id}")
        seen_run_ids.add(str(run_id))
        results.append(result)
    return results


def _load_json(path: pathlib.Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _runtime_profile_ref(record: dict[str, Any], repo_root=None) -> str:
    profile_id = record.get("runtime_profile", {}).get("profile_id")
    matches: list[pathlib.Path] = []
    root = pathlib.Path(repo_root) if repo_root is not None else REPO_ROOT / "artifacts" / "experiments"
    for path in sorted((root / "runtime_profiles").glob("*.json")):
        data = _load_json(path, "runtime profile")
        if data.get("profile_id") == profile_id:
            matches.append(path)
    if len(matches) != 1:
        raise ValueError(f"Runtime profile identity is not uniquely resolvable: {profile_id!r}")
    return f"runtime_profiles/{matches[0].name}"


def _resolve_evidence_path(
    relative_path: str, pair: SourcePair, runs_dir: pathlib.Path
) -> pathlib.Path:
    rel = pathlib.PurePosixPath(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"Evidence path is not confined: {relative_path!r}")
    roots: list[pathlib.Path] = [pair.record_path.parent]
    current = pair.record_path.parent.parent
    boundary = runs_dir.resolve()
    while True:
        try:
            current.resolve().relative_to(boundary)
        except ValueError:
            break
        roots.append(current)
        if current == boundary or current == current.parent:
            break
        current = current.parent
    matches = []
    for root in roots:
        candidate = (root / pathlib.Path(*rel.parts)).resolve()
        try:
            candidate.relative_to(boundary)
        except ValueError:
            continue
        matches.append(candidate)
    existing = [path for path in dict.fromkeys(matches) if path.is_file()]
    if len(existing) != 1:
        raise ValueError(
            f"Evidence path must resolve to exactly one file: {relative_path!r}; found={len(existing)}"
        )
    return existing[0]


def verify_source_pair(
    pair: SourcePair, runs_dir: pathlib.Path, *, resolved_configuration=None, repo_root=None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fail closed before blinding any scoreable execution pair."""
    record = _load_json(pair.record_path, "run record")
    result = _load_json(pair.result_path, "execution result")
    require_valid(record, "run_record.schema.json", str(pair.record_path))
    require_valid(result, "run_execution_result.schema.json", str(pair.result_path))
    if record["run_id"] != result["run_id"]:
        raise ValueError(f"Run ID mismatch in {pair.record_path.parent}")
    if result["final_outcome_code"] != "COMPLETED_VALID":
        return record, result
    if result.get("result_persisted") is not True:
        raise ValueError(f"Completed source is not marked persisted: {pair.result_path}")
    if result.get("run_record") != record:
        raise ValueError(f"Embedded run_record differs from sibling record.json: {pair.record_path.parent}")
    if result.get("parsed_candidate_output") != record.get("agent_output"):
        raise ValueError(f"Parsed candidate differs from canonical agent output: {pair.record_path.parent}")
    if pathlib.PurePosixPath(str(result.get("run_record_path", ""))).name != "record.json":
        raise ValueError(f"run_record_path does not identify record.json: {pair.result_path}")

    config_id = record["configuration"]["configuration_id"]
    root = pathlib.Path(repo_root) if repo_root is not None else REPO_ROOT / "artifacts" / "experiments"
    config = resolved_configuration or load_validated_configuration(
        f"configurations/{config_id}.v0.1.0.json",
        repo_root=root,
        runtime_profile_ref=_runtime_profile_ref(record, root),
    )
    validate_run_record_semantics(
        record, config, repo_root=root
    )

    for attempt in result.get("attempts", []):
        required = [("request_body_path", "request_body_sha256")]
        if attempt.get("adapter_status") == "SUCCESS":
            required.extend(
                [
                    ("model_output_path", "model_output_sha256"),
                    ("raw_response_path", "raw_response_sha256"),
                ]
            )
        if attempt.get("returned_provider") is not None:
            required.append(("provider_envelope_path", "provider_envelope_sha256"))
        for path_field, hash_field in required:
            rel_path = attempt.get(path_field)
            expected_hash = attempt.get(hash_field)
            if not isinstance(rel_path, str) or not isinstance(expected_hash, str):
                raise ValueError(
                    f"Attempt {attempt.get('attempt_index')} lacks required evidence pair "
                    f"{path_field}/{hash_field}"
                )
            evidence = _resolve_evidence_path(rel_path, pair, runs_dir)
            if _file_sha256(evidence) != expected_hash:
                raise ValueError(f"Evidence hash mismatch for {path_field}: {evidence}")
    return record, result


def _assert_confined_target(target: pathlib.Path, allowed_root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    root = allowed_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = target.resolve(strict=False)
    if target.parent != root:
        raise ValueError(f"Output must be one direct batch directory under {root}; got {target}")
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing calibration batch: {target}")
    current = root
    while current != current.parent:
        if current.is_symlink():
            raise ValueError(f"Symlink is not allowed in calibration output path: {current}")
        current = current.parent
    return target, root


def validate_bundle_manifest(manifest: dict[str, Any]) -> None:
    expected = {"schema_version", "blind_batch_id", "batch_created_at", "item_count", "items"}
    if set(manifest) != expected:
        raise ValueError(f"Bundle manifest fields differ from closed contract: {sorted(manifest)}")
    if manifest["schema_version"] != "0.1.0":
        raise ValueError("Unsupported bundle manifest schema_version")
    if not isinstance(manifest["blind_batch_id"], str) or not _BATCH_RE.fullmatch(
        manifest["blind_batch_id"]
    ):
        raise ValueError("Invalid blind_batch_id in bundle manifest")
    if not isinstance(manifest["batch_created_at"], str) or not manifest["batch_created_at"]:
        raise ValueError("Invalid batch_created_at in bundle manifest")
    items = manifest["items"]
    if not isinstance(items, list) or manifest["item_count"] != len(items) or not items:
        raise ValueError("Bundle manifest must describe at least one item and match item_count")
    ids = [item.get("blinded_item_id") for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate blinded_item_id in bundle manifest")
    for item in items:
        if set(item) != {"blinded_item_id", "relative_path", "item_content_sha256"}:
            raise ValueError("Bundle manifest item has unexpected fields")
        if not isinstance(item["blinded_item_id"], str) or not _BLIND_ITEM_RE.fullmatch(
            item["blinded_item_id"]
        ):
            raise ValueError("Bundle manifest contains an invalid blinded item ID")
        if not isinstance(item["item_content_sha256"], str) or not _SHA256_RE.fullmatch(
            item["item_content_sha256"]
        ):
            raise ValueError("Bundle manifest contains an invalid item hash")
        rel = pathlib.PurePosixPath(item["relative_path"])
        expected_name = f"{item['blinded_item_id']}.json"
        if rel.parts != ("items", expected_name):
            raise ValueError(
                "Bundle item path must be the direct canonical items/<blind-id>.json path"
            )


def build_calibration_batch(
    *,
    runs_dir: pathlib.Path,
    output_dir: pathlib.Path,
    allowed_output_root: pathlib.Path,
    batch_id: str,
    hmac_key: bytes,
    invocation_plan: dict[str, Any] | None = None,
    method_snapshot: dict[str, Any] | None = None,
    repo_root: pathlib.Path | None = None,
    _final_access=None,
) -> tuple[int, int]:
    """Validate all sources, then atomically publish one complete batch directory."""
    if not _BATCH_RE.fullmatch(batch_id):
        raise ValueError("batch_id must match ^BATCH-[A-Z0-9_-]+$")
    if len(hmac_key) < 32:
        raise ValueError("HMAC key must contain at least 32 bytes")
    output_dir, allowed_root = _assert_confined_target(output_dir, allowed_output_root)
    runs_dir = runs_dir.resolve()
    if invocation_plan is not None and invocation_plan.get("batch_id") != batch_id:
        raise ValueError("Blinding batch_id differs from invocation plan batch_id")
    if method_snapshot is not None and invocation_plan is None:
        raise ValueError("Selected runtime resolution requires its invocation plan")
    root = pathlib.Path(repo_root) if repo_root is not None else REPO_ROOT / "artifacts" / "experiments"
    if invocation_plan is not None and invocation_plan["execution_mode"] == "evaluation":
        from ..execution.final_access import require_access_binding
        require_access_binding(_final_access, plan=invocation_plan, method=method_snapshot,
                               repo_root=root, output_root=runs_dir)
    if invocation_plan is not None:
        require_valid(invocation_plan, "invocation_plan.schema.json", "invocation plan")
        coverage = verify_result_coverage(
            invocation_plan, discover_execution_results(runs_dir)
        )
        if not coverage["complete"]:
            raise ValueError(
                "Invocation plan is incomplete before blinding: "
                f"missing={coverage['missing_run_ids']}"
            )

    prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []
    skipped: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    created_at = datetime.now(timezone.utc).isoformat()

    for pair in discover_run_pairs(runs_dir):
        resolved_configuration = None
        if method_snapshot is not None:
            from ..execution.planned_execution import prepare_invocation
            identity = _load_json(pair.result_path, "execution result")["run_id"]
            resolved_configuration, _, _ = prepare_invocation(
                plan=invocation_plan, run_id=identity, method=method_snapshot,
                repo_root=root, output_root=runs_dir, _final_access=_final_access)
        record, result = verify_source_pair(pair, runs_dir, resolved_configuration=resolved_configuration, repo_root=root)
        if result.get("execution_mode") == "evaluation" and invocation_plan is None:
            raise ValueError("Evaluation run cannot be blinded without its frozen invocation plan")
        if result["final_outcome_code"] != "COMPLETED_VALID" or result["result_persisted"] is not True:
            skipped.append({
                "source_pair": pair.record_path.parent.relative_to(runs_dir).as_posix(),
                "reason": f"not_scoreable:{result['final_outcome_code']}",
            })
            continue

        source_run_id = record["run_id"]
        blind_id = generate_blind_id(source_run_id, batch_id, hmac_key)
        if blind_id in seen_ids:
            raise ValueError(f"Blind-ID collision for source run {source_run_id}")
        seen_ids.add(blind_id)
        item = build_blinded_item(
            run_record=record,
            normalized_state_before=project_state(record, "before"),
            normalized_state_after=project_state(record, "after"),
            blinded_item_id=blind_id,
            batch_id=batch_id,
        )
        leakage = verify_no_leakage(item)
        if leakage:
            raise ValueError(f"Leakage in {pair.record_path}: " + "; ".join(leakage))
        require_valid(item, "blinded_item.schema.json", blind_id)

        configuration_id = record["configuration"]["configuration_id"]
        if result.get("configuration_id") not in (None, configuration_id):
            raise ValueError(f"Configuration mismatch in {pair.record_path.parent}")
        scenario_id = record["scenario"]["scenario_id"]
        if result.get("scenario_id") not in (None, scenario_id):
            raise ValueError(f"Scenario mismatch in {pair.record_path.parent}")

        sealed_entry = {
            "blinded_item_id": blind_id,
            "source_run_id": source_run_id,
            "source_run_record_path": pair.record_path.relative_to(runs_dir).as_posix(),
            "source_result_record_path": pair.result_path.relative_to(runs_dir).as_posix(),
            "scenario_id": scenario_id,
            "configuration_id": configuration_id,
            "replication_index": result.get("replication_index"),
            "source_record_sha256": _file_sha256(pair.record_path),
            "source_result_sha256": _file_sha256(pair.result_path),
            "blinded_item_sha256": item["item_content_sha256"],
        }
        prepared.append((item, sealed_entry))

    if not prepared:
        raise ValueError("No COMPLETED_VALID persisted run pair is available for scoring")
    prepared.sort(key=lambda pair: pair[0]["blinded_item_id"])

    bundle_entries = [
        {
            "blinded_item_id": item["blinded_item_id"],
            "relative_path": f"items/{item['blinded_item_id']}.json",
            "item_content_sha256": item["item_content_sha256"],
        }
        for item, _ in prepared
    ]
    bundle_manifest = {
        "schema_version": "0.1.0",
        "blind_batch_id": batch_id,
        "batch_created_at": created_at,
        "item_count": len(prepared),
        "items": bundle_entries,
    }
    validate_bundle_manifest(bundle_manifest)
    sealed_manifest = {
        "schema_version": "0.1.0",
        "blind_batch_id": batch_id,
        "batch_created_at": created_at,
        "entries": [entry for _, entry in prepared],
    }
    require_valid(sealed_manifest, "blinding_manifest.schema.json", "sealed manifest")

    stage = pathlib.Path(tempfile.mkdtemp(prefix=".wp6-batch-", dir=allowed_root))
    try:
        bundle_dir = stage / "calibration_bundle"
        for item, _ in prepared:
            write_atomic(bundle_dir / "items" / f"{item['blinded_item_id']}.json", item)
        write_atomic(bundle_dir / "bundle_manifest.json", bundle_manifest)
        write_atomic(stage / "sealed" / "blinding_manifest.json", sealed_manifest)
        if skipped:
            technical = stage / "technical"
            technical.mkdir(parents=True, exist_ok=True)
            ledger = "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in skipped)
            (technical / "skip_ledger.jsonl").write_text(ledger, encoding="utf-8")
        (stage / "scores").mkdir(parents=True, exist_ok=True)
        for attempt in range(5):
            try:
                os.replace(stage, output_dir)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return len(prepared), len(skipped)


def build_final_bundle(*, gate_path, plan, method, repo_root, runs_dir,
                       output_dir, allowed_output_root, hmac_key):
    """Recheck the final gate and full source ledger before publishing blind items."""
    from ..execution.final_access import verify_final_access
    from ..execution.reconciliation import _reconcile_planned
    context = dict(plan=plan, method=method, repo_root=repo_root, output_root=runs_dir)
    access = verify_final_access(gate_path=gate_path, **context)
    ledger = _reconcile_planned(**context, _final_access=access)
    if not ledger["coverage"]["complete"]:
        raise ValueError("Incomplete producer ledger blocks final blinding")
    counts = build_calibration_batch(runs_dir=runs_dir, output_dir=output_dir,
        allowed_output_root=allowed_output_root, batch_id=plan["batch_id"], hmac_key=hmac_key,
        invocation_plan=plan, method_snapshot=method, repo_root=repo_root, _final_access=access)
    from ..execution.planned_execution import _save_exclusive
    target = pathlib.Path(output_dir)
    receipt = {"record_type": "FINAL_BLIND_BUNDLE_RECEIPT", "version": "0.1.0",
        "final_gate_sha256": access.gate_sha256, "plan_sha256": plan["plan_sha256"],
        "producer_reconciliation_sha256": compute_sha256(ledger),
        "bundle_manifest_sha256": _file_sha256(target / "calibration_bundle/bundle_manifest.json"),
        "mapping_sha256": _file_sha256(target / "sealed/blinding_manifest.json")}
    receipt["receipt_sha256"] = compute_sha256(receipt)
    _save_exclusive(target / "final_bundle_receipt.json", receipt)
    return counts


def verify_final_bundle(*, batch_dir, access, reconciliation):
    """Bind the produced blind bundle without opening its sealed treatment mapping."""
    from ..execution.reconciliation import _read, _confined
    from .score_items import load_bundle_items
    root = pathlib.Path(batch_dir).resolve()
    receipt = _read(_confined(root, "final_bundle_receipt.json"))
    if (receipt.get("record_type") != "FINAL_BLIND_BUNDLE_RECEIPT"
            or receipt.get("receipt_sha256") != compute_sha256({k: v for k, v in receipt.items() if k != "receipt_sha256"})
            or receipt.get("final_gate_sha256") != access.gate_sha256
            or receipt.get("plan_sha256") != access.plan_sha256
            or receipt.get("producer_reconciliation_sha256") != compute_sha256(reconciliation)
            or receipt.get("bundle_manifest_sha256") != _file_sha256(_confined(root, "calibration_bundle/bundle_manifest.json"))
            or receipt.get("mapping_sha256") != _file_sha256(_confined(root, "sealed/blinding_manifest.json"))):
        raise ValueError("Final blind bundle/source commitment mismatch")
    items = load_bundle_items(_confined(root, "calibration_bundle"), None)
    if len(items) != sum(r["final_outcome_code"] == "COMPLETED_VALID" for r in reconciliation["results"]):
        raise ValueError("Final blind item count differs from eligible producer inventory")
    return receipt


def _load_hmac_key(env_name: str) -> bytes:
    value = os.environ.get(env_name)
    if not value:
        raise ValueError(f"Required HMAC key environment variable is missing: {env_name}")
    try:
        key = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{env_name} must contain hexadecimal bytes") from exc
    if len(key) < 32:
        raise ValueError(f"{env_name} must contain at least 32 bytes")
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a canonical WP6 calibration batch")
    parser.add_argument("--runs-dir", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--calibration-root", default=str(DEFAULT_CALIBRATION_ROOT))
    parser.add_argument("--hmac-key-env", default="THESIS_BLINDING_HMAC_KEY")
    parser.add_argument("--invocation-plan", required=True)
    args = parser.parse_args(argv)
    root = pathlib.Path(args.calibration_root)
    target = root / args.batch_id
    try:
        valid, skipped = build_calibration_batch(
            runs_dir=pathlib.Path(args.runs_dir),
            output_dir=target,
            allowed_output_root=root,
            batch_id=args.batch_id,
            hmac_key=_load_hmac_key(args.hmac_key_env),
            invocation_plan=_load_json(pathlib.Path(args.invocation_plan), "invocation plan"),
        )
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print(f"[OK] Published {target}: scoreable={valid}, skipped={skipped}")
    print(f"[RATER INPUT ONLY] {target / 'calibration_bundle'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
