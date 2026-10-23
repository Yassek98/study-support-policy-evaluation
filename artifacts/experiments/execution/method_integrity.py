"""Conservative executable-method inventory, distinct from held-out evidence.

Includes current and compatibility code in the three execution packages rather
than guessing that an uncalled helper is irrelevant. Never enumerates runs, tmp,
archives, credentials, or final scenario content. A separate seal binds final data.
"""
import hashlib
from pathlib import Path

from .configuration_loader import read_canonical_artifact_bytes


HASH_MODE = "SHA256_UTF8_LF_NORMALIZED_TEXT"


def method_file_sha256(path):
    """Normalize text line endings only, matching treatment fingerprint semantics.

    Git core.autocrlf can alter a checkout's line endings. Raw request/response and
    retained evidence hashes are NOT computed here and remain byte-exact.
    """
    return hashlib.sha256(read_canonical_artifact_bytes(Path(path))).hexdigest()


def required_method_paths(repository_root):
    root = Path(repository_root).resolve()
    exp = root / "artifacts/experiments"
    paths = {root / "requirements.txt", root / "requirements-lock.txt", root / ".python-version",
             exp / "requirements.txt", exp / "EXPERIMENT_PROTOCOL_V0.md",
             exp / "validate_experiments.py"}
    for directory in ("execution", "evaluation", "validation"):
        paths.update((exp / directory).rglob("*.py"))
    for directory in ("configurations", "runtime_profiles", "policies", "schemas", "oracle",
                      "taxonomy", "fixtures", "benchmarks", "scenarios/development", "evaluation/rubrics"):
        paths.update((exp / directory).rglob("*.json"))
    paths.update((exp / "prompts/modules").glob("*.md"))
    paths.update((exp / "evaluation").glob("*.html"))
    for path in paths:
        path.resolve().relative_to(root)
    return sorted(path.relative_to(root).as_posix() for path in paths)


def verify_method_snapshot(method, *, repository_root):
    """Reject omitted dependencies as well as changed bytes; never refresh hashes."""
    root = Path(repository_root).resolve()
    if method.get("frozen_artifact_hash_mode") != HASH_MODE:
        raise ValueError("Unsupported or undeclared method-text hash mode")
    entries = method["frozen_artifacts"]
    inventory = {entry["path"]: entry["sha256"] for entry in entries}
    if len(inventory) != len(entries):
        raise ValueError("Duplicate frozen dependency")
    required = set(required_method_paths(root))
    if set(inventory) != required:
        raise ValueError("Frozen executable dependency inventory is incomplete or stale")
    for relative, fingerprint in inventory.items():
        path = (root / relative).resolve()
        path.relative_to(root)
        if not path.is_file() or method_file_sha256(path) != fingerprint:
            raise ValueError(f"Frozen dependency changed: {relative}")
    return {"verified_artifacts": len(inventory),
            "scope": "LF-normalized UTF-8 method text and dependency files; not held-out seal or installed environment"}
