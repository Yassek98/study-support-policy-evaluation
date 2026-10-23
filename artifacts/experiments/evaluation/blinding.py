"""
blinding.py — Pipeline di Blinding per Item di Valutazione

Costruisce, verifica e persiste gli item ciechi (blinded items) che vengono
consegnati al rater. Nessun metadato di configurazione, nessun nome di policy,
nessun riferimento a run sorgente deve mai comparire nel payload cieco.

Garanzie:
- FORBIDDEN_KEYS elenca esplicitamente tutti i campi vietati.
- verify_no_leakage() scansiona ricorsivamente sia chiavi sia valori stringa.
- write_atomic() garantisce che scritture parziali non corrompano il bundle.
- compute_sha256() è deterministico (sort_keys, separatori canonici).
"""
from __future__ import annotations

import hashlib
import copy
import hmac
import json
import os
import pathlib
import re
from typing import Any

# ---------------------------------------------------------------------------
# Costanti pubbliche
# ---------------------------------------------------------------------------

FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "run_id",
    "scenario_id",
    "configuration_id",
    "configuration_version",
    "policy_id",
    "terminal_state",
    "oracle_ref",
    "runtime_profile",
    "model",
    "model_slug",
    "provider",
    "level_a_result",
    "level_b_result",
    "level_a_outcome",
    "level_b_outcome",
    "pol01_output",
    "pol02_output",
    "pol03_output",
    "pol04_output",
    "pol05_output",
    "scaffold_state",
    "action_record",
    "safety_state",
    "prompt_fingerprint",
    "config_fingerprint",
    "expected_terminal_state",
    "oracle_verdict",
})

# Exact treatment labels are forbidden in values. Broad model-family substrings
# are intentionally not blocked because user and response text stay verbatim.
_FORBIDDEN_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(token)}(?![A-Za-z0-9_-])", re.IGNORECASE)
    for token in (
        "POL-01", "POL-02", "POL-03", "POL-04", "POL-05",
        "B0_SIMPLE", "S_P1", "S_P2", "S_P3",
        "G_P1_P2", "G_P1_P3", "G_P2_P3", "B1_FULL",
    )
)

# ---------------------------------------------------------------------------
# Funzioni pubbliche
# ---------------------------------------------------------------------------

def compute_sha256(data: dict[str, Any]) -> str:
    """Calcola lo SHA-256 canonico di un dict serializzato come JSON.

    La serializzazione è deterministica: chiavi ordinate, nessun spazio,
    encoding UTF-8, ensure_ascii=False.

    Args:
        data: Il dict da hashare. Non viene modificato.

    Returns:
        Stringa esadecimale di 64 caratteri rappresentante lo SHA-256.
    """
    canonical = json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def generate_blind_id(
    source_run_id: str,
    batch_id: str,
    hmac_key: bytes,
) -> str:
    """Genera l'identificatore cieco di un item come BITEM-<64 hex chars>.

    L'ID è determinato da HMAC-SHA256(key, '{batch_id}:{source_run_id}').
    La stessa coppia (batch_id, source_run_id) produce sempre lo stesso
    blinded_item_id con la stessa chiave, garantendo riproducibilità interna
    senza esporre il run_id al rater.

    Args:
        source_run_id: Identificatore del run sorgente (non incluso nel payload cieco).
        batch_id:      Identificatore del batch corrente (es. "BATCH-001").
        hmac_key:      Chiave segreta HMAC come bytes. Non deve essere loggata.

    Returns:
        Stringa nel formato "BITEM-" + 64 caratteri esadecimali.
    """
    message = f"{batch_id}:{source_run_id}".encode("utf-8")
    digest = hmac.new(hmac_key, message, hashlib.sha256).hexdigest()
    return f"BITEM-{digest}"


def build_blinded_item(
    run_record: dict[str, Any],
    normalized_state_before: dict[str, str],
    normalized_state_after: dict[str, str],
    blinded_item_id: str,
    batch_id: str,
) -> dict[str, Any]:
    """Costruisce il payload cieco da consegnare al rater.

    Il payload include esclusivamente le informazioni necessarie alla
    valutazione (testo utente, fatti dichiarati, risposta del sistema,
    stati normalizzati). Tutti i metadati di configurazione, policy,
    runtime e oracle sono ESCLUSI.

    Schema del payload:
        schema_version         : '0.3.0' for shared-context records; '0.2.0' for historical input
        blind_batch_id         : batch_id
        blinded_item_id        : blinded_item_id
        turn_context           : {
            user_message           : str,
            prior_user_declared_facts : list,
            normalized_state_before : dict (NormalizedState),
        }
        system_response        : str
        normalized_state_after : dict (NormalizedState)
        item_content_sha256    : SHA-256 del payload senza questo campo

    Args:
        run_record:              Il run_record canonico grezzo. Non viene modificato.
        normalized_state_before: NormalizedState prima del turno (dict a 5 campi).
        normalized_state_after:  NormalizedState dopo il turno  (dict a 5 campi).
        blinded_item_id:         ID cieco generato da generate_blind_id().
        batch_id:                Identificatore del batch corrente.

    Returns:
        Dict che rappresenta il blinded item pronto per write_atomic().
        Contiene sempre il campo item_content_sha256.

    Raises:
        KeyError: se i campi attesi (system_response, user_message) non sono
                  reperibili nel run_record con nessuna delle chiavi candidate.
    """
    # Estrai user_message
    user_message: str = (
        run_record.get("scenario", {}).get("user_message")
        or run_record.get("input", {}).get("user_message")
        or run_record.get("user_message", "")
    )

    # Only initial-state material supplied to this invocation is admissible.
    # Do not use final/model-authored facts or scenario author oracle notes.
    initial = run_record.get("initial_state", {})
    prior_facts: list[Any] = []
    for key in ("action_record", "scaffold_state"):
        state = initial.get(key)
        if isinstance(state, dict):
            facts = state.get("grounded_facts", [])
            if not isinstance(facts, list) or not all(isinstance(f, str) for f in facts):
                raise ValueError("Initial grounded facts must be a list of strings")
            prior_facts.extend(f for f in facts if f not in prior_facts)
    action = initial.get("action_record")
    prior_action = None
    if isinstance(action, dict) and action.get("action_text"):
        prior_action = {
            "text": action["action_text"],
            "status": action["action_status"],
            "acceptance_evidence": action.get("explicit_user_evidence"),
        }
    shared = initial.get("shared_context")
    if shared is not None:
        # Canonical ingestion validates this object against the scenario. Never
        # reconstruct richer context from metadata unavailable to the model.
        prior_facts = list(shared["prior_facts"])
        prior_action = copy.deepcopy(shared["prior_action"])

    # Estrai system_response
    system_response: str = run_record.get("agent_output", {}).get("system_response", "")
    if not isinstance(user_message, str) or not user_message.strip():
        raise ValueError("Canonical run record has no non-empty scenario.user_message")
    if not isinstance(system_response, str) or not system_response.strip():
        raise ValueError("Canonical run record has no non-empty agent_output.system_response")
    if not isinstance(prior_facts, list) or not all(isinstance(v, str) for v in prior_facts):
        raise ValueError("Initial-state grounded_facts must be a list of strings")

    # Costruzione payload senza sha256 (necessario per hash)
    payload_without_hash: dict[str, Any] = {
        "schema_version": "0.3.0" if shared is not None else "0.2.0",
        "blind_batch_id": batch_id,
        "blinded_item_id": blinded_item_id,
        "turn_context": {
            "user_message": user_message,
            "prior_user_declared_facts": list(prior_facts),
            "prior_action": prior_action,
            "normalized_state_before": dict(normalized_state_before),
        },
        "system_response": system_response,
        "normalized_state_after": dict(normalized_state_after),
    }

    if shared is not None:
        payload_without_hash["turn_context"]["objective"] = shared["objective"]
        payload_without_hash["turn_context"]["prior_plan"] = copy.deepcopy(shared["prior_plan"])
    sha = compute_sha256(payload_without_hash)
    return {**payload_without_hash, "item_content_sha256": sha}


def _check_value_patterns(value: str) -> list[str]:
    """Controlla se una stringa matcha pattern di leakage noti.

    Args:
        value: La stringa da controllare.

    Returns:
        Lista di pattern riscontrati (vuota se nessuno).
    """
    return [pattern.pattern for pattern in _FORBIDDEN_VALUE_PATTERNS if pattern.search(value)]


def verify_no_leakage(blinded_item: dict[str, Any]) -> list[str]:
    """Verifica ricorsivamente che il blinded item non contenga dati vietati.

    Scansione su:
    - Tutte le CHIAVI (qualsiasi livello di annidamento) → FORBIDDEN_KEYS.
    - Tutti i VALORI stringa → pattern in _FORBIDDEN_VALUE_PATTERNS.

    Args:
        blinded_item: Il payload cieco costruito da build_blinded_item().

    Returns:
        Lista di stringhe che descrivono ogni violazione trovata.
        Lista vuota = nessun leakage rilevato.
    """
    violations: list[str] = []

    def _recurse(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                current_path = f"{path}.{k}" if path else k
                if k in FORBIDDEN_KEYS:
                    violations.append(
                        f"Chiave vietata trovata: '{k}' @ {current_path}"
                    )
                _recurse(v, current_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _recurse(item, f"{path}[{i}]")
        elif isinstance(obj, str):
            matched = _check_value_patterns(obj)
            for pat in matched:
                violations.append(
                    f"Pattern vietato '{pat}' trovato nel valore @ {path}: {obj!r}"
                )

    _recurse(blinded_item, "")
    return violations


def write_atomic(path: pathlib.Path, data: dict[str, Any]) -> None:
    """Scrive atomicamente un dict come JSON nella path specificata.

    La scrittura avviene su un file temporaneo `<nome>.tmp` nella stessa
    directory, poi il file viene rinominato atomicamente. Questo impedisce
    che scritture parziali corrompano il bundle in caso di errori.
    La directory padre viene creata automaticamente se non esiste.

    Args:
        path: Path completo del file di destinazione (pathlib.Path).
        data: Il dict da serializzare come JSON (indent=2, ensure_ascii=False).

    Raises:
        OSError: se la scrittura o la rinomina falliscono.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(path))
    except Exception:
        # Pulizia best-effort del .tmp in caso di errore
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
