"""
Modulo per la composizione deterministica e la tracciabilità crittografica dei system prompt.
Funzione pura: riceve un manifest di configurazione e la root del repository,
valida l'intera configurazione via load_validated_configuration (fail-closed),
e produce un ComposedPrompt con ordine canonico immutabile dei moduli,
prompt_sha256 e artifact_fingerprint esteso.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Union

try:
    from .configuration_loader import (
        ValidatedConfiguration,
        compute_artifact_fingerprint,
        load_validated_configuration,
    )
except ImportError:
    from execution.configuration_loader import (
        ValidatedConfiguration,
        compute_artifact_fingerprint,
        load_validated_configuration,
    )


# Moduli canonici e relative posizioni nell'ordine rigido di composizione
CANONICAL_MODULE_ORDER = [
    ("BASE_AGENT", "prompts/modules/BASE_AGENT.v0.1.0.md"),
    ("POL-04_AUTONOMY_GATE", "prompts/modules/POL-04_AUTONOMY_GATE.v0.1.0.md"),
    ("POL-05_HUMAN_BOUNDARY", "prompts/modules/POL-05_HUMAN_BOUNDARY.v0.1.0.md"),
    ("POL-01_IF_THEN_PLAN", "prompts/modules/POL-01_IF_THEN_PLAN.v0.1.0.md"),
    ("POL-02_GRADED_SCAFFOLD", "prompts/modules/POL-02_GRADED_SCAFFOLD.v0.1.0.md"),
    ("POL-03_ACCEPTED_NEXT_ACTION", "prompts/modules/POL-03_ACCEPTED_NEXT_ACTION.v0.1.0.md"),
    ("OUTPUT_AND_TRACE_CONTRACT", "prompts/modules/OUTPUT_AND_TRACE_CONTRACT.v0.1.0.md"),
]

MODULE_ID_TO_REL_PATH = dict(CANONICAL_MODULE_ORDER)
REL_PATH_TO_MODULE_ID = {v: k for k, v in CANONICAL_MODULE_ORDER}

MODULE_SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True)
class ComposedPrompt:
    configuration_id: str
    configuration_version: str
    ordered_module_ids: List[str]
    module_hashes: Dict[str, str]
    prompt_text: str
    prompt_sha256: str
    artifact_fingerprint: str


def compute_sha256(text: str) -> str:
    """Calcola l'hash SHA-256 esadecimale di una stringa con encoding UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_newlines(text: str) -> str:
    """Normalizza tutti i newline a LF canonico (\n) e rimuove trailing whitespace isolati riga per riga."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    return "\n".join(line.rstrip() for line in lines).strip()


def compose_prompt(
    config_input: Union[Dict[str, Any], Path, str],
    repo_root: Union[Path, str, None] = None,
) -> ComposedPrompt:
    """
    Funzione pura di composizione del prompt.
    Riceve un manifest (dict, Path o str) e la root del repository.
    Carica e valida la configurazione in modalità fail-closed tramite
    load_validated_configuration(), verifica la presenza e il sandboxing
    dei file di modulo, e compone il prompt nell'ordine immutabile:

    1. BASE_AGENT
    2. POL-04_AUTONOMY_GATE (Safety Envelope)
    3. POL-05_HUMAN_BOUNDARY (Safety Envelope)
    4. POL-01_IF_THEN_PLAN (se P1_POL01 == True)
    5. POL-02_GRADED_SCAFFOLD (se P2_POL02 == True)
    6. POL-03_ACCEPTED_NEXT_ACTION (se P3_POL03 == True)
    7. OUTPUT_AND_TRACE_CONTRACT
    """
    if repo_root is None:
        root = Path(__file__).resolve().parent.parent
    else:
        root = Path(repo_root).resolve()

    if not root.exists():
        raise FileNotFoundError(f"Repository root non trovata: {root}")

    # 1. Validazione canonica fail-closed unificata (schema, fattori, binding, sandboxing)
    val_cfg: ValidatedConfiguration = load_validated_configuration(config_input, repo_root=root)

    p1 = val_cfg.experimental_factors["P1_POL01"]
    p2 = val_cfg.experimental_factors["P2_POL02"]
    p3 = val_cfg.experimental_factors["P3_POL03"]

    # 2. Moduli attivi determinati dai fattori sperimentali
    active_module_ids = [
        "BASE_AGENT",
        "POL-04_AUTONOMY_GATE",
        "POL-05_HUMAN_BOUNDARY",
    ]
    if p1:
        active_module_ids.append("POL-01_IF_THEN_PLAN")
    if p2:
        active_module_ids.append("POL-02_GRADED_SCAFFOLD")
    if p3:
        active_module_ids.append("POL-03_ACCEPTED_NEXT_ACTION")
    active_module_ids.append("OUTPUT_AND_TRACE_CONTRACT")

    # 3. Caricamento deterministico dei moduli nell'ordine canonico
    ordered_ids: List[str] = []
    module_hashes: Dict[str, str] = {}
    module_texts: List[str] = []

    for mid in active_module_ids:
        rel_path = MODULE_ID_TO_REL_PATH[mid]
        full_path = (root / rel_path).resolve()

        # Sandboxing del modulo
        try:
            full_path.relative_to(root)
        except ValueError:
            raise PermissionError(f"Path traversal rilevato per il modulo '{mid}': {rel_path}")

        if not full_path.exists():
            raise FileNotFoundError(f"File del modulo di prompt mancante su disco: {full_path}")

        with open(full_path, "r", encoding="utf-8") as mf:
            raw_text = mf.read()

        norm_text = normalize_newlines(raw_text)
        mod_hash = compute_sha256(norm_text)

        ordered_ids.append(mid)
        module_hashes[mid] = mod_hash
        module_texts.append(norm_text)

    composed_text = MODULE_SEPARATOR.join(module_texts)
    composed_hash = compute_sha256(composed_text)
    artifact_hash = compute_artifact_fingerprint(val_cfg, composed_text, repo_root=root)

    return ComposedPrompt(
        configuration_id=val_cfg.configuration_id,
        configuration_version=val_cfg.version,
        ordered_module_ids=ordered_ids,
        module_hashes=module_hashes,
        prompt_text=composed_text,
        prompt_sha256=composed_hash,
        artifact_fingerprint=artifact_hash,
    )
