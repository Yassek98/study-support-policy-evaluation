"""
Caricatore e validatore canonico per le configurazioni sperimentali (manifest).

Fornisce la funzione `load_validated_configuration` e la classe immutabile
`ValidatedConfiguration`, garantendo fail-closed e assenza di bypass.
"""

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union
from urllib.parse import urlparse
import sys

# Prevenzione dual-import class divergence per ValidatedConfiguration
if __name__ == "artifacts.experiments.execution.configuration_loader":
    sys.modules.setdefault("execution.configuration_loader", sys.modules[__name__])
elif __name__ == "execution.configuration_loader":
    sys.modules.setdefault("artifacts.experiments.execution.configuration_loader", sys.modules[__name__])

import jsonschema

try:
    from validation.manifests import (
        CANONICAL_FACTORIAL_MATRIX,
        get_canonical_manifest_bindings,
    )
except ImportError:
    from artifacts.experiments.validation.manifests import (
        CANONICAL_FACTORIAL_MATRIX,
        get_canonical_manifest_bindings,
    )


def freeze_data(obj: Any) -> Any:
    """Trasforma ricorsivamente dizionari in MappingProxyType e collezioni in tuple per immutabilita profonda."""
    if isinstance(obj, (dict, MappingProxyType)):
        return MappingProxyType({k: freeze_data(v) for k, v in obj.items()})
    elif isinstance(obj, (list, tuple)):
        return tuple(freeze_data(x) for x in obj)
    return obj


def unfreeze_data(obj: Any) -> Any:
    """Riconverte ricorsivamente MappingProxyType e tuple in strutture serializzabili standard."""
    if isinstance(obj, (dict, MappingProxyType)):
        return {k: unfreeze_data(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [unfreeze_data(x) for x in obj]
    return obj


def read_canonical_artifact_bytes(path: Path) -> bytes:
    """Read a UTF-8 text artifact with platform-independent line endings."""
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Artefatto testuale non UTF-8: {path}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


@dataclass(frozen=True)
class ValidatedConfiguration:
    """Rappresentazione immutabile e tipizzata di una configurazione validata."""
    configuration_id: str
    version: str
    status: str
    architecture: MappingProxyType
    experimental_factors: MappingProxyType
    support_policy_refs: Tuple[str, ...]
    safety_policy_refs: Tuple[str, ...]
    prompt_modules: MappingProxyType
    state_schema_refs: Tuple[str, ...]
    output_schema_refs: Tuple[str, ...]
    oracle_refs: Tuple[str, ...]
    runtime_profile_ref: str
    freeze_status: str
    runtime_profile: MappingProxyType
    raw_dict: MappingProxyType
    initial_state_defaults_ref: str = "configurations/DEFAULT_INITIAL_STATES.v0.1.0.json"
    initial_state_defaults: MappingProxyType = MappingProxyType({})


def load_validated_configuration(
    config_input: Union[Dict[str, Any], Path, str],
    repo_root: Union[Path, str] = None,
    runtime_profile_ref: str = "runtime_profiles/DEV_MOCK.v0.1.0.json",
) -> ValidatedConfiguration:
    """
    Carica e valida in modo rigoroso e fail-closed una configurazione sperimentale.

    Applica 5 livelli di controllo autoritativi:
    1. Convalida sintattica e strutturale rispetto a configuration.schema.json;
    2. Corrispondenza biiettiva con la matrice canonica 2^3;
    3. Conformita assoluta del safety envelope invariante;
    4. Verifica biiettiva ed esatta dei binding di policy, prompt, schemi e oracoli;
    5. Selezione separata e validazione fail-closed del runtime profile richiesto dall'esecuzione.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    else:
        repo_root = Path(repo_root).resolve()

    if isinstance(config_input, ValidatedConfiguration):
        return config_input

    manifest_data: Dict[str, Any]
    if isinstance(config_input, (str, Path)):
        p = Path(config_input)
        if p.is_absolute():
            cfg_path = p.resolve()
        else:
            cfg_path = (repo_root / p).resolve()

        try:
            cfg_path.relative_to(repo_root)
        except ValueError as exc:
            raise PermissionError(
                f"Accesso negato: il percorso del manifest '{cfg_path}' non e confinato nella root autorizzata '{repo_root}'"
            ) from exc

        if not cfg_path.is_file():
            alt_candidate = (repo_root / "configurations" / f"{p.name}.v0.1.0.json").resolve()
            if alt_candidate.is_file():
                cfg_path = alt_candidate
            else:
                raise FileNotFoundError(f"File manifest di configurazione non trovato: {cfg_path}")

        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Manifest JSON malformato in '{cfg_path}': {exc}") from exc
    elif isinstance(config_input, dict):
        manifest_data = copy.deepcopy(config_input)
    else:
        raise TypeError(f"Tipo di input non supportato per config_input: {type(config_input)}")

    # 1. Convalida strutturale JSON Schema
    schema_path = repo_root / "schemas" / "configuration.schema.json"
    if not schema_path.is_file():
        raise FileNotFoundError(f"Schema di configurazione non trovato: {schema_path}")

    with open(schema_path, "r", encoding="utf-8") as sf:
        cfg_schema = json.load(sf)

    try:
        jsonschema.validate(instance=manifest_data, schema=cfg_schema)
    except jsonschema.ValidationError as exc:
        raise ValueError(
            f"Manifest di configurazione non conforme a configuration.schema.json: {exc.message}"
        ) from exc

    cfg_id = manifest_data["configuration_id"]
    factors = manifest_data["experimental_factors"]
    p1 = factors["P1_POL01"]
    p2 = factors["P2_POL02"]
    p3 = factors["P3_POL03"]

    # 2. Corrispondenza con la matrice canonica 2^3
    if cfg_id not in CANONICAL_FACTORIAL_MATRIX:
        raise ValueError(f"Configurazione sconosciuta o non presente nella matrice canonica 2^3: {cfg_id}")

    expected_factors = CANONICAL_FACTORIAL_MATRIX[cfg_id]
    if (p1, p2, p3) != expected_factors:
        raise ValueError(
            f"Fattori per '{cfg_id}' discordanti dalla matrice: trovati {(p1, p2, p3)}, attesi {expected_factors}"
        )

    # 3. Verifica dei binding esatti
    canonical_bindings = get_canonical_manifest_bindings(p1, p2, p3)

    if sorted(manifest_data["support_policy_refs"]) != canonical_bindings["support_policy_refs"]:
        raise ValueError(
            f"Binding 'support_policy_refs' non conforme per '{cfg_id}': atteso {canonical_bindings['support_policy_refs']}"
        )

    if sorted(manifest_data["safety_policy_refs"]) != canonical_bindings["safety_policy_refs"]:
        raise ValueError(
            f"Binding 'safety_policy_refs' non conforme per '{cfg_id}': atteso {canonical_bindings['safety_policy_refs']}"
        )

    if sorted(manifest_data["prompt_modules"]["support"]) != canonical_bindings["prompt_support"]:
        raise ValueError(
            f"Binding 'prompt_modules.support' non conforme per '{cfg_id}': atteso {canonical_bindings['prompt_support']}"
        )

    if sorted(manifest_data["prompt_modules"]["safety"]) != canonical_bindings["prompt_safety"]:
        raise ValueError(
            f"Binding 'prompt_modules.safety' non conforme per '{cfg_id}': atteso {canonical_bindings['prompt_safety']}"
        )

    if sorted(manifest_data["state_schema_refs"]) != canonical_bindings["state_schema_refs"]:
        raise ValueError(
            f"Binding 'state_schema_refs' non conforme per '{cfg_id}': atteso {canonical_bindings['state_schema_refs']}"
        )

    if sorted(manifest_data["output_schema_refs"]) != canonical_bindings["output_schema_refs"]:
        raise ValueError(
            f"Binding 'output_schema_refs' non conforme per '{cfg_id}': atteso {canonical_bindings['output_schema_refs']}"
        )

    if sorted(manifest_data["oracle_refs"]) != canonical_bindings["oracle_refs"]:
        raise ValueError(
            f"Binding 'oracle_refs' non conforme per '{cfg_id}': atteso {canonical_bindings['oracle_refs']}"
        )

    # 4. Verifica dei percorsi, confinamento ed esistenza dei file referenziati
    all_refs = (
        [manifest_data["prompt_modules"]["base"], manifest_data["prompt_modules"]["output_contract"]]
        + manifest_data["prompt_modules"]["support"]
        + manifest_data["prompt_modules"]["safety"]
        + manifest_data["support_policy_refs"]
        + manifest_data["safety_policy_refs"]
        + manifest_data["state_schema_refs"]
        + manifest_data["output_schema_refs"]
        + manifest_data["oracle_refs"]
    )
    all_refs.append(runtime_profile_ref)

    for ref in all_refs:
        ref_path = (repo_root / ref).resolve()
        try:
            ref_path.relative_to(repo_root)
        except ValueError as exc:
            raise PermissionError(
                f"Riferimento '{ref}' in '{cfg_id}' viola il confinamento nella root della repository"
            ) from exc

        if not ref_path.is_file():
            raise FileNotFoundError(
                f"File referenziato '{ref}' in configurazione '{cfg_id}' non trovato su disco"
            )

    # 5. Convalida dello schema del runtime profile
    prof_path = (repo_root / runtime_profile_ref).resolve()
    prof_schema_path = repo_root / "schemas" / "runtime_profile.schema.json"
    if not prof_schema_path.is_file():
        raise FileNotFoundError(f"Schema runtime_profile non trovato: {prof_schema_path}")

    with open(prof_schema_path, "r", encoding="utf-8") as psf:
        prof_schema = json.load(psf)

    with open(prof_path, "r", encoding="utf-8") as pf:
        prof_data = json.load(pf)

    try:
        jsonschema.validate(instance=prof_data, schema=prof_schema)
    except jsonschema.ValidationError as exc:
        raise ValueError(
            f"Runtime profile '{runtime_profile_ref}' selezionato per '{cfg_id}' non conforme a runtime_profile.schema.json: {exc.message}"
        ) from exc

    # 6. Caricamento e confinamento degli stati iniziali di default
    defaults_ref = manifest_data.get("initial_state_defaults_ref", "configurations/DEFAULT_INITIAL_STATES.v0.1.0.json")
    def_path = (repo_root / defaults_ref).resolve()
    try:
        def_path.relative_to(repo_root)
    except ValueError as exc:
        raise PermissionError(
            f"Accesso negato: il percorso initial_state_defaults_ref '{def_path}' non e confinato nella root autorizzata"
        ) from exc

    if not def_path.is_file():
        raise FileNotFoundError(f"File default initial states non trovato: {def_path}")

    with open(def_path, "r", encoding="utf-8") as df:
        defaults_data = json.load(df)

    return ValidatedConfiguration(
        configuration_id=cfg_id,
        version=manifest_data["version"],
        status=manifest_data["status"],
        architecture=freeze_data(manifest_data["architecture"]),
        experimental_factors=freeze_data(manifest_data["experimental_factors"]),
        support_policy_refs=freeze_data(manifest_data["support_policy_refs"]),
        safety_policy_refs=freeze_data(manifest_data["safety_policy_refs"]),
        prompt_modules=freeze_data(manifest_data["prompt_modules"]),
        state_schema_refs=freeze_data(manifest_data["state_schema_refs"]),
        output_schema_refs=freeze_data(manifest_data["output_schema_refs"]),
        oracle_refs=freeze_data(manifest_data["oracle_refs"]),
        runtime_profile_ref=runtime_profile_ref,
        initial_state_defaults_ref=defaults_ref,
        freeze_status=manifest_data["freeze_status"],
        runtime_profile=freeze_data(prof_data),
        initial_state_defaults=freeze_data(defaults_data),
        raw_dict=freeze_data(manifest_data),
    )


def _collect_schema_dependency_closure(
    schema_refs: Tuple[str, ...],
    repo_root: Path,
) -> Tuple[Path, ...]:
    """Restituisce la chiusura deterministica dei JSON Schema locali referenziati.

    Il fingerprint non puo limitarsi agli schemi elencati direttamente nel
    manifest: anche un cambiamento in un ``$ref`` transitivo modifica il
    contratto effettivo. I riferimenti esterni o fuori repository sono quindi
    respinti in modo fail-closed.
    """
    pending = [(repo_root / ref).resolve() for ref in schema_refs]
    visited: Dict[str, Path] = {}

    while pending:
        schema_path = pending.pop()
        try:
            relative_path = schema_path.relative_to(repo_root).as_posix()
        except ValueError as exc:
            raise PermissionError(
                f"Schema fuori dalla root autorizzata: {schema_path}"
            ) from exc

        if relative_path in visited:
            continue
        if not schema_path.is_file():
            raise FileNotFoundError(f"Schema referenziato non trovato: {schema_path}")

        try:
            schema_data = json.loads(schema_path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"JSON Schema malformato in '{schema_path}': {exc}") from exc

        visited[relative_path] = schema_path

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and not ref.startswith("#"):
                    ref_without_fragment = ref.split("#", 1)[0]
                    parsed = urlparse(ref_without_fragment)
                    if parsed.scheme:
                        if parsed.scheme not in {"http", "https"} or parsed.netloc != "local.thesis.invalid":
                            raise ValueError(
                                f"$ref esterno non consentito in '{schema_path}': {ref}"
                            )
                        dependency = (repo_root / "schemas" / Path(parsed.path).name).resolve()
                    elif ref_without_fragment.startswith("/"):
                        dependency = (repo_root / ref_without_fragment.lstrip("/")).resolve()
                    else:
                        dependency = (schema_path.parent / ref_without_fragment).resolve()
                    pending.append(dependency)
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)

        visit(schema_data)

    return tuple(visited[key] for key in sorted(visited))


def compute_artifact_fingerprint(
    validated_config: ValidatedConfiguration,
    prompt_text: str,
    repo_root: Union[Path, str] = None,
) -> str:
    """
    Calcola l'impronta crittografica estesa SHA-256 (artifact_fingerprint).

    Include:
    - Hash del testo composito normalizzato (prompt_text)
    - Hash deterministico del manifest JSON normalizzato
    - Hash deterministici di tutti i file di policy referenziati (in ordine canonico)
    - Hash deterministici degli schemi diretti e di tutti i loro $ref locali transitivi
    - Hash deterministici di tutti gli oracoli referenziati (in ordine canonico)
    Il runtime e deliberatamente escluso: possiede un fingerprint separato nel run record e non
    deve modificare l'identita del trattamento sperimentale.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    else:
        repo_root = Path(repo_root).resolve()

    hasher = hashlib.sha256()

    # 1. Prompt composito normalizzato
    hasher.update(prompt_text.encode("utf-8"))

    # 2. Manifest canonico
    manifest_clean = unfreeze_data(validated_config.raw_dict)
    manifest_bytes = json.dumps(manifest_clean, sort_keys=True, ensure_ascii=False).encode("utf-8")
    hasher.update(hashlib.sha256(manifest_bytes).digest())

    # 3. Policy files
    for pol_ref in sorted(list(validated_config.safety_policy_refs) + list(validated_config.support_policy_refs)):
        content = read_canonical_artifact_bytes(repo_root / pol_ref)
        hasher.update(hashlib.sha256(content).digest())

    # 4. Schemas: chiusura transitiva dei $ref locali, senza duplicati.
    direct_schema_refs = tuple(
        sorted(set(validated_config.state_schema_refs) | set(validated_config.output_schema_refs)
               | {"schemas/shared_context.schema.json"})
    )
    for schema_path in _collect_schema_dependency_closure(direct_schema_refs, repo_root):
        content = read_canonical_artifact_bytes(schema_path)
        hasher.update(hashlib.sha256(content).digest())

    # 5. Oracles
    for orc_ref in sorted(list(validated_config.oracle_refs)):
        content = read_canonical_artifact_bytes(repo_root / orc_ref)
        hasher.update(hashlib.sha256(content).digest())

    # 6. Initial state defaults
    if validated_config.initial_state_defaults_ref:
        def_path = (repo_root / validated_config.initial_state_defaults_ref).resolve()
        if def_path.is_file():
            hasher.update(hashlib.sha256(read_canonical_artifact_bytes(def_path)).digest())

    return hasher.hexdigest()
