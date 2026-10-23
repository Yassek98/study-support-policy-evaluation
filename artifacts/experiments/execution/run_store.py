"""
Modulo per la persistenza atomica, sicura e confinata dei risultati di esecuzione del runner.
Garantisce:
1. Sandboxing assoluto rispetto a output_root (anti-path traversal).
2. Segregazione rigida tra modalita development ed evaluation.
3. Prevenzione attiva delle collisioni su run_id (scrittura immutabile / fail-closed).
4. Persistenza atomica tramite file temporanei e os.replace.
5. Calcolo crittografico SHA-256 byte-exact dei payload raw.
"""

import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple, Union

try:
    from .run_record_validator import _get_compiled_validator
except ImportError:
    from artifacts.experiments.execution.run_record_validator import _get_compiled_validator



class RunStore:
    """Store atomico e sandboxed per payload raw, run records e result ledgers."""

    def __init__(
        self,
        output_root: Union[str, Path],
        execution_mode: str = "development",
    ):
        self.output_root = Path(output_root).resolve()
        self.execution_mode = execution_mode

        # Quarantena simmetrica di partizione
        path_parts = tuple(part.casefold() for part in self.output_root.parts)

        def contains_partition(first: str, second: str) -> bool:
            return any(
                path_parts[index : index + 2] == (first, second)
                for index in range(len(path_parts) - 1)
            )

        is_development_partition = contains_partition("runs", "development")
        is_evaluation_partition = contains_partition("runs", "evaluation")
        if self.execution_mode in ("development", "pilot"):
            if is_evaluation_partition:
                raise ValueError(
                    f"Violazione di quarantena: execution_mode='{self.execution_mode}' non puo scrivere in 'runs/evaluation': '{self.output_root}'"
                )
        elif self.execution_mode == "evaluation":
            if is_development_partition:
                raise ValueError(
                    f"Violazione di quarantena: execution_mode='evaluation' non puo scrivere in percorsi di development: '{self.output_root}'"
                )
            if not is_evaluation_partition:
                raise ValueError(
                    f"Violazione di quarantena: execution_mode='evaluation' deve essere confinato in una partizione 'runs/evaluation', ricevuto: '{self.output_root}'"
                )
        else:
            raise ValueError(f"Modalita di esecuzione non supportata per RunStore: '{self.execution_mode}'")

        self._ensure_directories()

    def _ensure_directories(self) -> None:
        self.raw_dir = self.output_root / "raw"
        self.results_dir = self.output_root / "results"
        self.committed_dir = self.output_root / "committed"
        self.staging_dir = self.output_root / ".staging"

        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.committed_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def _verify_sandboxing(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.output_root)
        except ValueError:
            raise ValueError(f"Violazione di confinamento (path traversal): {path}")
        return resolved

    def check_run_id_collision(self, run_id: str) -> None:
        """Verifica se il run_id e' gia' presente nel result ledger."""
        target = self.results_dir / f"{run_id}.json"
        committed_target = self.committed_dir / run_id
        if target.exists() or committed_target.exists():
            raise FileExistsError(f"Collisione run_id: risultato per '{run_id}' gia' esistente in {target}")

    def _write_atomic_raw_file(
        self,
        rel_path_str: str,
        payload_bytes: bytes,
    ) -> Tuple[str, str]:
        """Salva atomicamente un file nella directory raw calcolandone l'hash SHA-256."""
        sha256 = hashlib.sha256(payload_bytes).hexdigest()
        abs_path = self.output_root / rel_path_str
        self._verify_sandboxing(abs_path)

        if abs_path.exists():
            raise FileExistsError(f"Impossibile sovrascrivere file raw esistente: {abs_path}")

        tmp_name = f".tmp_{Path(rel_path_str).name}_{uuid.uuid4().hex}"
        tmp_path = self.raw_dir / tmp_name
        try:
            with open(tmp_path, "wb") as f:
                f.write(payload_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, abs_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        return rel_path_str, sha256

    def save_request_body(
        self,
        run_id: str,
        attempt_index: int,
        payload: Union[str, bytes, Dict[str, Any]],
    ) -> Tuple[str, str]:
        """
        Salva il corpo esatto della richiesta (senza header HTTP di autorizzazione o segreti).
        Restituisce la tupla (relative_path_string, sha256_hex).
        """
        if isinstance(payload, dict):
            payload_bytes = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        elif isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = bytes(payload)

        rel_path_str = f"raw/{run_id}_attempt_{attempt_index}.request.json"
        return self._write_atomic_raw_file(rel_path_str, payload_bytes)

    def save_provider_envelope(
        self,
        run_id: str,
        attempt_index: int,
        payload: Union[str, bytes],
    ) -> Tuple[str, str]:
        """
        Salva i byte esatti della risposta HTTP del provider (envelope).
        Restituisce la tupla (relative_path_string, sha256_hex).
        """
        if isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = bytes(payload)

        rel_path_str = f"raw/{run_id}_attempt_{attempt_index}.provider_envelope.bin"
        return self._write_atomic_raw_file(rel_path_str, payload_bytes)

    def save_model_output(
        self,
        run_id: str,
        attempt_index: int,
        payload: Union[str, bytes],
    ) -> Tuple[str, str]:
        """
        Salva i byte esatti del payload estratto del modello (assistant content JSON).
        Restituisce la tupla (relative_path_string, sha256_hex).
        """
        if isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = bytes(payload)

        rel_path_str = f"raw/{run_id}_attempt_{attempt_index}.model_output.bin"
        return self._write_atomic_raw_file(rel_path_str, payload_bytes)

    def save_raw_payload(
        self,
        run_id: str,
        attempt_index: int,
        payload: Union[str, bytes],
    ) -> Tuple[str, str]:
        """
        Metodo di compatibilità: salva il model output e restituisce (relative_path, sha256).
        """
        if isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = bytes(payload)

        rel_path_str = f"raw/{run_id}_attempt_{attempt_index}.raw.bin"
        return self._write_atomic_raw_file(rel_path_str, payload_bytes)

    def _validate_result_ledger_payload(
        self,
        result_data: Dict[str, Any],
        expected_result_path: str,
    ) -> bytes:
        """Valida lo stato dichiarato dal ledger e restituisce JSON UTF-8 standard."""
        # Invariante: il ledger salvato su disco deve dichiarare se stesso come persistito
        if (
            result_data.get("result_persisted") is not True
            or result_data.get("result_path") != expected_result_path
        ):
            raise ValueError(
                f"Incoerenza stato ledger: il result salvato su disco deve dichiarare "
                f"result_persisted=True e result_path='{expected_result_path}', trovato "
                f"result_persisted={result_data.get('result_persisted')} e "
                f"result_path='{result_data.get('result_path')}'"
            )

        VALID_ADAPTER_STATUSES = {"SUCCESS", "TRANSPORT_ERROR", "TIMEOUT", "PROVIDER_ERROR", "UNKNOWN"}
        for att in result_data.get("attempts", []):
            st = att.get("adapter_status")
            if st not in VALID_ADAPTER_STATUSES:
                raise ValueError(f"adapter_status non valido: '{st}'. Valori ammessi: {sorted(VALID_ADAPTER_STATUSES)}")

        schemas_dir = (Path(__file__).resolve().parent.parent / "schemas").resolve()
        val = _get_compiled_validator("run_execution_result.schema.json", str(schemas_dir))
        errors = list(val.iter_errors(result_data))
        if errors:
            raise ValueError(f"Result ledger non conforme a run_execution_result.schema.json: {errors[0].message}")

        return (
            json.dumps(result_data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        ).encode("utf-8")

    def save_result_ledger(
        self,
        run_id: str,
        result_data: Dict[str, Any],
    ) -> str:
        """
        Salva un ledger senza run record in results/{run_id}.json.
        I risultati COMPLETED_VALID usano save_completed_bundle per pubblicazione atomica con il record.
        """
        rel_path_str = f"results/{run_id}.json"
        abs_path = self.output_root / rel_path_str
        self._verify_sandboxing(abs_path)

        if abs_path.exists():
            raise FileExistsError(f"Impossibile sovrascrivere result ledger esistente: {abs_path}")

        payload_bytes = self._validate_result_ledger_payload(result_data, rel_path_str)
        tmp_name = f".tmp_res_{run_id}_{uuid.uuid4().hex}"
        tmp_path = self.results_dir / tmp_name
        try:
            with open(tmp_path, "wb") as f:
                f.write(payload_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, abs_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        return rel_path_str

    def save_completed_bundle(
        self,
        run_id: str,
        record_data: Dict[str, Any],
        result_data: Dict[str, Any],
    ) -> Tuple[str, str]:
        """
        Pubblica record e ledger COMPLETED_VALID come singola directory atomica.

        Prima del solo os.replace finale entrambi gli artefatti vivono sotto .staging/;
        un fallimento o crash non puo quindi lasciare un record nel namespace committed/.
        """
        target_dir = self.committed_dir / run_id
        self._verify_sandboxing(target_dir)
        if target_dir.exists():
            raise FileExistsError(f"Collisione bundle completato per run_id '{run_id}': {target_dir}")

        record_rel_path = f"committed/{run_id}/record.json"
        result_rel_path = f"committed/{run_id}/result.json"
        if result_data.get("run_record_path") != record_rel_path:
            raise ValueError(
                f"run_record_path deve essere '{record_rel_path}', ricevuto "
                f"{result_data.get('run_record_path')!r}"
            )
        if result_data.get("run_record") != record_data:
            raise ValueError("Il run_record incorporato nel ledger diverge dal record da pubblicare")

        result_bytes = self._validate_result_ledger_payload(result_data, result_rel_path)
        record_bytes = (
            json.dumps(record_data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        ).encode("utf-8")

        staging_run_dir = self.staging_dir / f"{run_id}_{uuid.uuid4().hex}"
        self._verify_sandboxing(staging_run_dir)
        staging_run_dir.mkdir(parents=False, exist_ok=False)
        try:
            for name, payload in (("record.json", record_bytes), ("result.json", result_bytes)):
                with open(staging_run_dir / name, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            for attempt in range(5):
                try:
                    os.replace(staging_run_dir, target_dir)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
        finally:
            if staging_run_dir.exists():
                shutil.rmtree(staging_run_dir, ignore_errors=True)

        return record_rel_path, result_rel_path
