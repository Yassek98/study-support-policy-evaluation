"""
Deterministic execution runner for development and evaluation workflows.
Implementa la pipeline a 15 stadi, l'anti-contaminazione per whitelist,
la tassonomia degli errori, il tracciamento dei tentativi e l'aggregazione dei token.
"""

import copy
import datetime
import hashlib
import json
import math
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
from jsonschema import Draft202012Validator

try:
    from artifacts.experiments.execution.configuration_loader import (
        ValidatedConfiguration,
        freeze_data,
        load_validated_configuration,
        unfreeze_data,
    )
    from artifacts.experiments.execution.prompt_composer import ComposedPrompt, compose_prompt
    from artifacts.experiments.execution.run_record_validator import (
        CanonicalScenario,
        _get_compiled_validator,
        compute_scenario_fingerprint,
        derive_final_state,
        load_canonical_scenario,
        project_initial_state,
        validate_agent_turn_output_semantics,
        validate_run_record_semantics,
    )
    from artifacts.experiments.execution.run_store import RunStore
    from artifacts.experiments.execution.output_assembler import AssemblyDecisionConflict, assemble_canonical_turn_output
    from artifacts.experiments.execution.structured_output import (
        build_openrouter_structured_output_contract,
        bundle_schema_with_defs,
        canonical_json_dumps,
        project_model_generated_schema,
        specialize_agent_turn_output_schema,
    )
except ImportError:
    from execution.configuration_loader import (
        ValidatedConfiguration,
        freeze_data,
        load_validated_configuration,
        unfreeze_data,
    )
    from execution.prompt_composer import ComposedPrompt, compose_prompt
    from execution.run_record_validator import (
        CanonicalScenario,
        _get_compiled_validator,
        compute_scenario_fingerprint,
        derive_final_state,
        load_canonical_scenario,
        project_initial_state,
        validate_agent_turn_output_semantics,
        validate_run_record_semantics,
    )
    from execution.run_store import RunStore
    from execution.output_assembler import AssemblyDecisionConflict, assemble_canonical_turn_output
    from execution.structured_output import (
        build_openrouter_structured_output_contract,
        bundle_schema_with_defs,
        canonical_json_dumps,
        project_model_generated_schema,
        specialize_agent_turn_output_schema,
    )


class RunOutcomeCode(str, Enum):
    """Tassonomia chiusa a 10 codici di esito per l'esecuzione del runner."""
    COMPLETED_VALID = "COMPLETED_VALID"
    OUTPUT_SEMANTICALLY_INVALID = "OUTPUT_SEMANTICALLY_INVALID"
    OUTPUT_SCHEMA_INVALID = "OUTPUT_SCHEMA_INVALID"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RUN_RECORD_INVALID = "RUN_RECORD_INVALID"
    INTERNAL_RUNNER_ERROR = "INTERNAL_RUNNER_ERROR"
    INPUT_CONTRACT_ERROR = "INPUT_CONTRACT_ERROR"


@dataclass(frozen=True)
class TokenUsage:
    """Conteggio deterministico dei token per tentativo ed aggregato."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class RetryPolicy:
    """Politica di retry versionata per guasti tecnici transitori."""
    policy_id: str = "DEFAULT_TECHNICAL_RETRY"
    max_attempts: int = 1
    backoff_factor: float = 0.0
    transient_errors: Tuple[str, ...] = ("TRANSPORT_ERROR", "TIMEOUT")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "max_attempts": self.max_attempts,
            "backoff_factor": self.backoff_factor,
            "transient_errors": list(self.transient_errors),
        }


def default_clock_source() -> str:
    """Restituisce il timestamp corrente UTC in formato ISO-8601 strict."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass(frozen=True)
class RunContext:
    """Parametri di ambiente e controllo per l'esecuzione del runner."""
    run_id: str
    protocol_version: str = "0.5.0"
    execution_mode: str = "development"
    replication_index: int = 1
    batch_id: Optional[str] = None
    output_root: Path = field(default_factory=lambda: Path("tmp/runs"))
    clock_source: Callable[[], str] = default_clock_source
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)


@dataclass(frozen=True)
class AdapterRequest:
    """Richiesta provider-neutral strutturata inviata all'adapter."""
    system_prompt: str
    user_message: str
    dialogue_context: MappingProxyType
    model: str
    provider: str
    temperature: Optional[float]
    seed: int
    max_tokens: int
    top_p: Optional[float] = None
    reasoning: Optional[MappingProxyType] = None
    stop_sequences: Tuple[str, ...] = ()
    turn_timestamp: Optional[str] = None
    endpoint_family: str = "LOCAL_MOCK"
    timeout_seconds: Optional[float] = None
    routing_policy: Optional[MappingProxyType] = None
    structured_output_contract: Optional[MappingProxyType] = None

    def build_canonical_request_body(self) -> Dict[str, Any]:
        """
        Costruisce il dizionario del payload JSON deterministico inviato al provider.
        Include esclusivamente parametri di runtime, messaggi e structured output;
        zero header HTTP, zero token segreti, zero oracoli o rubriche.
        """
        messages = [
            {"role": "system", "content": self.system_prompt}
        ]
        if self.dialogue_context and len(self.dialogue_context) > 0:
            user_content_dict: Dict[str, Any] = {
                "dialogue_context": unfreeze_data(self.dialogue_context),
                "user_message": self.user_message,
            }
            user_content_str = json.dumps(
                user_content_dict,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            messages.append({"role": "user", "content": user_content_str})
        else:
            messages.append({"role": "user", "content": self.user_message})

        body: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "seed": self.seed,
            "max_tokens": self.max_tokens,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.top_p is not None:
            body["top_p"] = self.top_p
        if self.reasoning is not None:
            body["reasoning"] = unfreeze_data(self.reasoning)
        if self.stop_sequences:
            body["stop"] = list(self.stop_sequences)
        if self.routing_policy is not None:
            r_dict = unfreeze_data(self.routing_policy)
            prov_body: Dict[str, Any] = {}
            if "provider_order" in r_dict:
                prov_body["order"] = r_dict["provider_order"]
            elif "order" in r_dict:
                prov_body["order"] = r_dict["order"]
            if "allow_fallbacks" in r_dict:
                prov_body["allow_fallbacks"] = r_dict["allow_fallbacks"]
            if "require_parameters" in r_dict:
                prov_body["require_parameters"] = r_dict["require_parameters"]
            if "data_collection" in r_dict:
                prov_body["data_collection"] = r_dict["data_collection"]
            body["provider"] = prov_body
        if self.structured_output_contract is not None:
            body["response_format"] = unfreeze_data(self.structured_output_contract)
        return body


@dataclass(frozen=True)
class RawAdapterResponse:
    """Risposta grezza restituita dall'adapter (mock o live)."""
    status: str  # "SUCCESS", "TIMEOUT", "TRANSPORT_ERROR", "PROVIDER_ERROR"
    raw_payload: Optional[Union[str, bytes]] = None
    model_output_payload: Optional[Union[str, bytes]] = None
    provider_envelope: Optional[Union[str, bytes]] = None
    http_status_code: Optional[int] = None
    latency_ms: float = 0.0
    error_message: Optional[str] = None
    token_usage: Optional[TokenUsage] = None
    requested_model: Optional[str] = None
    returned_model: Optional[str] = None
    requested_provider_order: Optional[Tuple[str, ...]] = None
    returned_provider: Optional[str] = None
    provider_response_id: Optional[str] = None

    def get_model_output_bytes(self) -> Optional[bytes]:
        """Restituisce i byte del payload del modello per il parser del runner."""
        payload = self.model_output_payload if self.model_output_payload is not None else self.raw_payload
        if payload is None:
            return None
        return payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)


@dataclass(frozen=True)
class AttemptRecord:
    """Tracciamento universale immutabile di un singolo tentativo di esecuzione."""
    attempt_index: int
    started_at: str
    completed_at: str
    adapter_status: str
    latency_ms: float
    raw_response_path: Optional[str] = None
    raw_response_sha256: Optional[str] = None
    token_usage: Optional[TokenUsage] = None
    error_code: Optional[str] = None
    error_details: Optional[str] = None
    request_body_path: Optional[str] = None
    request_body_sha256: Optional[str] = None
    provider_envelope_path: Optional[str] = None
    provider_envelope_sha256: Optional[str] = None
    model_output_path: Optional[str] = None
    model_output_sha256: Optional[str] = None
    http_status_code: Optional[int] = None
    requested_model: Optional[str] = None
    returned_model: Optional[str] = None
    requested_provider_order: Optional[Tuple[str, ...]] = None
    returned_provider: Optional[str] = None
    provider_response_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "attempt_index": self.attempt_index,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "adapter_status": self.adapter_status,
            "latency_ms": self.latency_ms,
            "raw_response_path": self.raw_response_path,
            "raw_response_sha256": self.raw_response_sha256,
            "error_code": self.error_code,
            "error_details": self.error_details,
        }
        if self.token_usage is not None:
            d["token_usage"] = self.token_usage.to_dict()
        if self.request_body_path is not None:
            d["request_body_path"] = self.request_body_path
            d["request_body_sha256"] = self.request_body_sha256
        if self.provider_envelope_path is not None:
            d["provider_envelope_path"] = self.provider_envelope_path
            d["provider_envelope_sha256"] = self.provider_envelope_sha256
        if self.model_output_path is not None:
            d["model_output_path"] = self.model_output_path
            d["model_output_sha256"] = self.model_output_sha256
        if self.http_status_code is not None:
            d["http_status_code"] = self.http_status_code
        if self.requested_model is not None:
            d["requested_model"] = self.requested_model
        if self.returned_model is not None:
            d["returned_model"] = self.returned_model
        if self.requested_provider_order is not None:
            d["requested_provider_order"] = list(self.requested_provider_order)
        if self.returned_provider is not None:
            d["returned_provider"] = self.returned_provider
        if self.provider_response_id is not None:
            d["provider_response_id"] = self.provider_response_id
        return d


@dataclass(frozen=True)
class RunExecutionResult:
    """Oggetto immutabile di risultato di esecuzione e ledger persistito."""
    run_id: str
    configuration_ref: str
    scenario_ref: str
    final_outcome_code: RunOutcomeCode
    retry_policy: RetryPolicy
    attempts: Tuple[AttemptRecord, ...]
    protocol_version: Optional[str] = None
    execution_mode: Optional[str] = None
    replication_index: Optional[int] = None
    batch_id: Optional[str] = None
    result_persisted: bool = False
    configuration_id: Optional[str] = None
    scenario_id: Optional[str] = None
    parsed_candidate_output: Optional[MappingProxyType] = None
    run_record: Optional[MappingProxyType] = None
    run_record_path: Optional[str] = None
    result_path: Optional[str] = None
    preflight_errors: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "run_id": self.run_id,
            "configuration_ref": self.configuration_ref,
            "scenario_ref": self.scenario_ref,
            "final_outcome_code": self.final_outcome_code.value,
            "retry_policy": self.retry_policy.to_dict(),
            "attempts": [a.to_dict() for a in self.attempts],
            "protocol_version": self.protocol_version,
            "execution_mode": self.execution_mode,
            "replication_index": self.replication_index,
            "batch_id": self.batch_id,
            "result_persisted": self.result_persisted,
        }
        if self.configuration_id is not None:
            d["configuration_id"] = self.configuration_id
        if self.scenario_id is not None:
            d["scenario_id"] = self.scenario_id
        if self.parsed_candidate_output is not None:
            d["parsed_candidate_output"] = unfreeze_data(self.parsed_candidate_output)
        if self.run_record is not None:
            d["run_record"] = unfreeze_data(self.run_record)
        if self.run_record_path is not None:
            d["run_record_path"] = self.run_record_path
        if self.result_path is not None:
            d["result_path"] = self.result_path
        if self.preflight_errors:
            d["preflight_errors"] = list(self.preflight_errors)
        return d


class BaseAdapter(ABC):
    """Interfaccia minima astratta dell'adapter per l'invocazione del modello."""
    @abstractmethod
    def execute(self, request: AdapterRequest) -> RawAdapterResponse:
        pass


@dataclass(frozen=True)
class ModelInput:
    """Proiezione pura dei soli campi autorizzati per il modello (anti-contaminazione)."""
    system_prompt: str
    user_message: str
    dialogue_context: MappingProxyType
    runtime_parameters: MappingProxyType
    turn_timestamp: Optional[str] = None


def project_model_input(
    scenario: CanonicalScenario,
    validated_configuration: ValidatedConfiguration,
    composed_prompt: ComposedPrompt,
    projected_initial_state: Dict[str, Any],
    turn_timestamp: Optional[str] = None,
) -> ModelInput:
    """
    Funzione pura che costruisce per WHITELIST l'input proiettato al modello.
    Garantisce l'esclusione categorica di: oracle_ref, rubric, expected_observables,
    forbidden_actions, source_claims, family, split, evaluated_policy e available_facts non dichiarati.
    """
    if projected_initial_state != project_initial_state(scenario, validated_configuration):
        raise ValueError("Model input state differs from canonical scenario projection")
    factors = validated_configuration.experimental_factors
    # The runner owns scenario identity. Exposing it to the model is unnecessary
    # and can leak the evaluated policy/family through human-readable IDs.
    context: Dict[str, Any] = {
        "safety_state": copy.deepcopy(projected_initial_state["safety_state"]),
        "shared_context": copy.deepcopy(projected_initial_state["shared_context"]),
    }
    if turn_timestamp is not None:
        context["turn_timestamp"] = turn_timestamp

    if factors.get("P2_POL02", False) and "scaffold_state" in projected_initial_state:
        context["scaffold_state"] = copy.deepcopy(projected_initial_state["scaffold_state"])

    if factors.get("P3_POL03", False) and "action_record" in projected_initial_state:
        if projected_initial_state["action_record"] is not None:
            context["action_record"] = copy.deepcopy(projected_initial_state["action_record"])

    rp = validated_configuration.runtime_profile
    runtime_params: Dict[str, Any] = {
        "model": rp["model"],
        "provider": rp["provider"],
        "seed": rp["seed"],
        "max_tokens": rp["max_tokens"],
    }
    if "temperature" in rp:
        runtime_params["temperature"] = rp["temperature"]
    if "top_p" in rp:
        runtime_params["top_p"] = rp["top_p"]
    if "reasoning" in rp:
        runtime_params["reasoning"] = unfreeze_data(rp["reasoning"])
    if "stop_sequences" in rp:
        runtime_params["stop_sequences"] = tuple(rp["stop_sequences"])
    if "endpoint_family" in rp:
        runtime_params["endpoint_family"] = rp["endpoint_family"]
    if "timeout_seconds" in rp:
        runtime_params["timeout_seconds"] = rp["timeout_seconds"]
    if "routing" in rp:
        runtime_params["routing"] = unfreeze_data(rp["routing"])
    if "structured_output" in rp:
        runtime_params["structured_output"] = unfreeze_data(rp["structured_output"])

    return ModelInput(
        system_prompt=composed_prompt.prompt_text,
        user_message=scenario.user_message,
        dialogue_context=freeze_data(context),
        runtime_parameters=freeze_data(runtime_params),
        turn_timestamp=turn_timestamp,
    )


TRANSIENT_PROVIDER_HTTP_STATUS_CODES = (429, 503)
VALID_ADAPTER_STATUSES = {"SUCCESS", "TRANSPORT_ERROR", "TIMEOUT", "PROVIDER_ERROR"}
ISO_8601_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)


def reject_nonstandard_json_constant(value: str) -> None:
    """Impedisce a json.loads di accettare le estensioni Python NaN/Infinity."""
    raise ValueError(f"Costante JSON non standard: {value}")


def reject_duplicate_json_keys(pairs):
    """Reject ambiguous objects at every depth, retaining the raw response separately."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def sample_clock_timestamp(clock_source: Callable[[], str]) -> str:
    """Campiona e valida un timestamp timezone-aware senza normalizzarlo o ripararlo."""
    value = clock_source()
    if not isinstance(value, str) or not ISO_8601_TIMESTAMP_PATTERN.fullmatch(value):
        raise ValueError(f"clock_source ha restituito un timestamp ISO-8601 non valido: {value!r}")
    parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"clock_source ha restituito un timestamp privo di timezone: {value!r}")
    return value


def validate_adapter_response_contract(response: Any) -> Optional[str]:
    """Restituisce un errore contrattuale senza dereferenziare risposte adapter malformate."""
    if not isinstance(response, RawAdapterResponse):
        return f"adapter deve restituire RawAdapterResponse, ricevuto {type(response).__name__}"
    payload_to_check = response.model_output_payload if response.model_output_payload is not None else response.raw_payload
    if payload_to_check is not None and not isinstance(payload_to_check, (str, bytes)):
        return f"payload del modello deve essere str, bytes oppure None, ricevuto {type(payload_to_check).__name__}"
    if response.provider_envelope is not None and not isinstance(response.provider_envelope, (str, bytes)):
        return f"provider_envelope deve essere str, bytes oppure None, ricevuto {type(response.provider_envelope).__name__}"
    if response.http_status_code is not None and type(response.http_status_code) is not int:
        return "http_status_code deve essere un intero oppure None"
    if (
        isinstance(response.latency_ms, bool)
        or not isinstance(response.latency_ms, (int, float))
        or not math.isfinite(response.latency_ms)
        or response.latency_ms < 0.0
    ):
        return f"latency_ms deve essere un numero finito >= 0.0, ricevuto {response.latency_ms!r}"
    if response.error_message is not None and not isinstance(response.error_message, str):
        return "error_message deve essere una stringa oppure None"
    if response.token_usage is not None:
        if not isinstance(response.token_usage, TokenUsage):
            return f"token_usage deve essere TokenUsage oppure None, ricevuto {type(response.token_usage).__name__}"
        for field_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(response.token_usage, field_name)
            if type(value) is not int or value < 0:
                return f"token_usage.{field_name} deve essere un intero >= 0, ricevuto {value!r}"
        if response.token_usage.total_tokens != (
            response.token_usage.prompt_tokens + response.token_usage.completion_tokens
        ):
            return (
                "token_usage.total_tokens deve coincidere con prompt_tokens + completion_tokens, "
                f"ricevuto {response.token_usage.total_tokens} != "
                f"{response.token_usage.prompt_tokens} + {response.token_usage.completion_tokens}"
            )
    return None


def _finalize_and_persist_result(
    res: RunExecutionResult,
    store: RunStore,
) -> RunExecutionResult:
    """
    Costruisce la rappresentazione definitiva con result_persisted=True e result_path,
    e la persiste atomicamente nel result ledger.
    Se la persistenza fallisce, restituisce un risultato fail-closed con result_persisted=False.
    """
    rel_path = f"results/{res.run_id}.json"
    final_res = RunExecutionResult(
        **{
            **res.__dict__,
            "protocol_version": res.protocol_version or getattr(store, "protocol_version", None),
            "execution_mode": res.execution_mode or store.execution_mode,
            "replication_index": res.replication_index or getattr(store, "replication_index", None),
            "batch_id": res.batch_id or getattr(store, "batch_id", None),
            "result_persisted": True,
            "result_path": rel_path,
        }
    )
    try:
        store.save_result_ledger(res.run_id, final_res.to_dict())
        return final_res
    except Exception as ex:
        return RunExecutionResult(
            **{
                **res.__dict__,
                "final_outcome_code": (
                    RunOutcomeCode.INTERNAL_RUNNER_ERROR
                    if res.final_outcome_code == RunOutcomeCode.COMPLETED_VALID
                    else res.final_outcome_code
                ),
                "result_persisted": False,
                "result_path": None,
                "preflight_errors": tuple(list(res.preflight_errors) + [f"Fallimento persistenza result ledger: {ex}"]),
            }
        )


def validate_execution_preconditions(run_context: RunContext) -> List[str]:
    """Valida contrattualmente RunContext e RetryPolicy prima di qualsiasi allocazione I/O."""
    errors: List[str] = []
    if not isinstance(run_context, RunContext):
        return [f"run_context deve essere RunContext, ricevuto {type(run_context).__name__}"]

    if not isinstance(run_context.run_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_\.-]+", run_context.run_id
    ):
        errors.append(
            f"run_id non valido o contenente caratteri non consentiti: {run_context.run_id!r}"
        )
    if not isinstance(run_context.protocol_version, str) or not run_context.protocol_version.strip():
        errors.append("protocol_version deve essere una stringa non vuota")
    if run_context.execution_mode not in ("development", "pilot", "evaluation"):
        errors.append(f"execution_mode non supportato: {run_context.execution_mode!r}")
    if type(run_context.replication_index) is not int or run_context.replication_index < 1:
        errors.append(
            f"replication_index deve essere un intero >= 1, ricevuto {run_context.replication_index!r}"
        )
    if run_context.batch_id is not None and (
        not isinstance(run_context.batch_id, str)
        or not re.fullmatch(r"BATCH-[A-Z0-9_-]+", run_context.batch_id)
    ):
        errors.append("batch_id deve essere None oppure rispettare ^BATCH-[A-Z0-9_-]+$")
    if not isinstance(run_context.output_root, (str, Path)):
        errors.append(
            f"output_root deve essere str o Path, ricevuto {type(run_context.output_root).__name__}"
        )
    if not callable(run_context.clock_source):
        errors.append("clock_source deve essere callable")
    rp = run_context.retry_policy
    if not isinstance(rp, RetryPolicy):
        errors.append(f"retry_policy deve essere RetryPolicy, ricevuto {type(rp).__name__}")
        return errors
    if type(rp.max_attempts) is not int or rp.max_attempts < 1:
        errors.append(
            f"retry_policy.max_attempts deve essere un intero >= 1, ricevuto {rp.max_attempts!r}"
        )
    if (
        isinstance(rp.backoff_factor, bool)
        or not isinstance(rp.backoff_factor, (int, float))
        or not math.isfinite(rp.backoff_factor)
        or rp.backoff_factor < 0.0
    ):
        errors.append(
            f"retry_policy.backoff_factor deve essere un numero finito >= 0.0, ricevuto {rp.backoff_factor!r}"
        )
    elif rp.backoff_factor != 0.0:
        errors.append(
            "retry_policy.backoff_factor deve essere 0.0 nel mock runner deterministico WP3; "
            "una strategia temporale non e' ancora implementata o approvata"
        )
    if not isinstance(rp.policy_id, str) or not rp.policy_id.strip():
        errors.append("retry_policy.policy_id deve essere una stringa non vuota")
    VALID_TRANSIENT_ERRORS = {"TIMEOUT", "TRANSPORT_ERROR", "PROVIDER_ERROR"}
    if not isinstance(rp.transient_errors, tuple):
        errors.append("retry_policy.transient_errors deve essere una tuple di codici ammessi")
    else:
        for err in rp.transient_errors:
            if not isinstance(err, str) or err not in VALID_TRANSIENT_ERRORS:
                errors.append(
                    f"transient_error non valido {err!r}. Valori ammessi: {sorted(VALID_TRANSIENT_ERRORS)}"
                )
    return errors


def execute_scenario(
    configuration_ref: Union[str, Path, ValidatedConfiguration],
    scenario_ref: Union[str, Path],
    adapter: BaseAdapter,
    run_context: RunContext,
    repo_root: Optional[Union[str, Path]] = None,
) -> RunExecutionResult:
    """
    Esegue in modo deterministico uno scenario rispetto a una configurazione
    attraverso la pipeline lineare a 13 stadi fail-closed.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    else:
        repo_root = Path(repo_root).resolve()

    cfg_ref_str = str(configuration_ref.configuration_id if isinstance(configuration_ref, ValidatedConfiguration) else configuration_ref)
    scen_ref_str = str(scenario_ref)

    if not isinstance(run_context, RunContext):
        return RunExecutionResult(
            run_id="invalid-run-context",
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            final_outcome_code=RunOutcomeCode.INPUT_CONTRACT_ERROR,
            retry_policy=RetryPolicy(),
            attempts=(),
            result_persisted=False,
            result_path=None,
            preflight_errors=(
                f"run_context deve essere RunContext, ricevuto {type(run_context).__name__}",
            ),
        )

    # 0. Preflight contrattuale fail-closed su RunContext e RetryPolicy (prima di qualsiasi I/O o setup)
    preflight_errors = validate_execution_preconditions(run_context)
    if not isinstance(adapter, BaseAdapter):
        preflight_errors.append(
            f"adapter deve implementare BaseAdapter, ricevuto {type(adapter).__name__}"
        )
    if preflight_errors:
        return RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            final_outcome_code=RunOutcomeCode.INPUT_CONTRACT_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=(),
            result_persisted=False,
            result_path=None,
            preflight_errors=tuple(preflight_errors),
        )

    # Inizializzazione run store
    try:
        store = RunStore(run_context.output_root, run_context.execution_mode)
        store.protocol_version = run_context.protocol_version
        store.replication_index = run_context.replication_index
        store.batch_id = run_context.batch_id
        store.check_run_id_collision(run_context.run_id)
    except (ValueError, FileExistsError) as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            final_outcome_code=RunOutcomeCode.INPUT_CONTRACT_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=(),
            result_persisted=False,
            preflight_errors=(str(ex),),
        )
        return res
    except Exception as ex:
        return RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=(),
            result_persisted=False,
            preflight_errors=(f"Errore interno inizializzazione RunStore: {ex}",),
        )

    # 1. Caricamento configurazione validata
    val_cfg: Optional[ValidatedConfiguration] = None
    if isinstance(configuration_ref, ValidatedConfiguration):
        val_cfg = configuration_ref
    else:
        try:
            val_cfg = load_validated_configuration(configuration_ref, repo_root=repo_root)
        except (ValueError, TypeError, FileNotFoundError) as ex:
            res = RunExecutionResult(
                run_id=run_context.run_id,
                configuration_ref=cfg_ref_str,
                scenario_ref=scen_ref_str,
                final_outcome_code=RunOutcomeCode.INPUT_CONTRACT_ERROR,
                retry_policy=run_context.retry_policy,
                attempts=(),
                result_persisted=False,
                preflight_errors=(f"Errore caricamento configurazione: {ex}",),
            )
            return _finalize_and_persist_result(res, store)
        except Exception as ex:
            res = RunExecutionResult(
                run_id=run_context.run_id,
                configuration_ref=cfg_ref_str,
                scenario_ref=scen_ref_str,
                final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
                retry_policy=run_context.retry_policy,
                attempts=(),
                result_persisted=False,
                preflight_errors=(f"Errore interno loader configurazione: {ex}",),
            )
            return _finalize_and_persist_result(res, store)

    # 2. Caricamento scenario canonico
    canonical_scen: Optional[CanonicalScenario] = None
    try:
        canonical_scen = load_canonical_scenario(
            scenario_ref,
            repo_root=repo_root,
            expected_execution_mode=run_context.execution_mode,
        )
    except (ValueError, TypeError, FileNotFoundError) as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id if val_cfg else None,
            final_outcome_code=RunOutcomeCode.INPUT_CONTRACT_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=(),
            result_persisted=False,
            preflight_errors=(f"Errore caricamento scenario: {ex}",),
        )
        return _finalize_and_persist_result(res, store)
    except Exception as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id if val_cfg else None,
            final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=(),
            result_persisted=False,
            preflight_errors=(f"Errore interno loader scenario: {ex}",),
        )
        return _finalize_and_persist_result(res, store)

    # 3. Proiezione dello stato iniziale per la configurazione
    try:
        projected_initial = project_initial_state(canonical_scen, val_cfg)
    except Exception as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=(),
            result_persisted=False,
            preflight_errors=(f"Errore interno proiezione initial_state: {ex}",),
        )
        return _finalize_and_persist_result(res, store)

    # 4. Composizione deterministica del prompt di sistema
    try:
        composed_prompt = compose_prompt(val_cfg, repo_root=repo_root)
    except Exception as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=(),
            result_persisted=False,
            preflight_errors=(f"Errore interno composizione prompt: {ex}",),
        )
        return _finalize_and_persist_result(res, store)

    # 5. Timestamp autorità
    try:
        turn_timestamp = sample_clock_timestamp(run_context.clock_source)
    except Exception as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=(),
            result_persisted=False,
            preflight_errors=(f"Errore interno clock_source per timestamp turno: {ex}",),
        )
        return _finalize_and_persist_result(res, store)

    # 6. Proiezione dell'input al modello (whitelist rigorosa)
    try:
        model_input = project_model_input(
            scenario=canonical_scen,
            validated_configuration=val_cfg,
            composed_prompt=composed_prompt,
            projected_initial_state=projected_initial,
            turn_timestamp=turn_timestamp,
        )
    except Exception as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=(),
            result_persisted=False,
            preflight_errors=(f"Errore interno proiezione ModelInput: {ex}",),
        )
        return _finalize_and_persist_result(res, store)

    rt_params = unfreeze_data(model_input.runtime_parameters)
    endpoint_family = rt_params.get("endpoint_family", "LOCAL_MOCK")
    timeout_seconds = rt_params.get("timeout_seconds")
    routing_policy = freeze_data(rt_params["routing"]) if "routing" in rt_params else None

    structured_output_contract = None
    model_generated_schema = None
    if "structured_output" in rt_params:
        st_cfg = rt_params["structured_output"]
        if st_cfg.get("mode") == "json_schema":
            try:
                bundled_schema, s_hash, b_hash = bundle_schema_with_defs(
                    root_schema_filename=Path(st_cfg.get("schema_ref", "agent_turn_output.schema.json")).name,
                    schemas_dir=repo_root / "schemas",
                )
                bundled_schema = specialize_agent_turn_output_schema(
                    bundled_schema,
                    unfreeze_data(val_cfg.experimental_factors),
                )
                bundled_schema = project_model_generated_schema(bundled_schema)
                model_generated_schema = bundled_schema
                so_contract = build_openrouter_structured_output_contract(
                    bundled_schema=bundled_schema,
                    schema_name="agent_turn_output",
                    strict=st_cfg.get("strict", True),
                )
                structured_output_contract = freeze_data(so_contract)
            except Exception as ex:
                res = RunExecutionResult(
                    run_id=run_context.run_id,
                    configuration_ref=cfg_ref_str,
                    scenario_ref=scen_ref_str,
                    configuration_id=val_cfg.configuration_id,
                    scenario_id=canonical_scen.scenario_id,
                    final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
                    retry_policy=run_context.retry_policy,
                    attempts=(),
                    result_persisted=False,
                    preflight_errors=(f"Errore interno bundling structured output: {ex}",),
                )
                return _finalize_and_persist_result(res, store)

    req = AdapterRequest(
        system_prompt=model_input.system_prompt,
        user_message=model_input.user_message,
        dialogue_context=model_input.dialogue_context,
        model=rt_params["model"],
        provider=rt_params["provider"],
        temperature=rt_params.get("temperature"),
        seed=rt_params["seed"],
        max_tokens=rt_params["max_tokens"],
        top_p=rt_params.get("top_p"),
        reasoning=freeze_data(rt_params["reasoning"]) if "reasoning" in rt_params else None,
        stop_sequences=tuple(rt_params.get("stop_sequences", ())),
        turn_timestamp=turn_timestamp,
        endpoint_family=endpoint_family,
        timeout_seconds=timeout_seconds,
        routing_policy=routing_policy,
        structured_output_contract=structured_output_contract,
    )

    # 7. Ciclo di tentativi e invocazione adapter
    attempts_list: List[AttemptRecord] = []
    max_attempts = run_context.retry_policy.max_attempts
    last_response: Optional[RawAdapterResponse] = None
    technical_error_code: Optional[RunOutcomeCode] = None

    for attempt_idx in range(1, max_attempts + 1):
        try:
            started_at = sample_clock_timestamp(run_context.clock_source)
        except Exception as ex:
            res = RunExecutionResult(
                run_id=run_context.run_id,
                configuration_ref=cfg_ref_str,
                scenario_ref=scen_ref_str,
                configuration_id=val_cfg.configuration_id,
                scenario_id=canonical_scen.scenario_id,
                final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
                retry_policy=run_context.retry_policy,
                attempts=tuple(attempts_list),
                result_persisted=False,
                preflight_errors=(f"Fallimento clock_source prima del tentativo {attempt_idx}: {ex}",),
            )
            return _finalize_and_persist_result(res, store)

        # Persistenza deterministica del request body (senza header/segreti)
        req_body_path: Optional[str] = None
        req_body_sha256: Optional[str] = None
        try:
            req_body_dict = req.build_canonical_request_body()
            req_body_path, req_body_sha256 = store.save_request_body(
                run_id=run_context.run_id,
                attempt_index=attempt_idx,
                payload=req_body_dict,
            )
        except Exception as ex:
            try:
                completed_at = sample_clock_timestamp(run_context.clock_source)
            except Exception:
                completed_at = started_at
            attempts_list.append(
                AttemptRecord(
                    attempt_index=attempt_idx,
                    started_at=started_at,
                    completed_at=completed_at,
                    adapter_status="UNKNOWN",
                    latency_ms=0.0,
                    error_code="INTERNAL_RUNNER_ERROR",
                    error_details=f"Fallimento persistenza request body prima della chiamata provider: {ex}",
                    requested_model=req.model,
                    requested_provider_order=tuple(req.routing_policy["provider_order"])
                    if req.routing_policy and "provider_order" in req.routing_policy
                    else None,
                )
            )
            res = RunExecutionResult(
                run_id=run_context.run_id,
                configuration_ref=cfg_ref_str,
                scenario_ref=scen_ref_str,
                configuration_id=val_cfg.configuration_id,
                scenario_id=canonical_scen.scenario_id,
                final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
                retry_policy=run_context.retry_policy,
                attempts=tuple(attempts_list),
                result_persisted=False,
            )
            return _finalize_and_persist_result(res, store)

        try:
            resp = adapter.execute(req)
        except Exception as ex:
            clock_error: Optional[Exception] = None
            try:
                completed_at = sample_clock_timestamp(run_context.clock_source)
            except Exception as clock_ex:
                completed_at = started_at
                clock_error = clock_ex
            error_details = f"Eccezione interna adapter non classificata: {type(ex).__name__}: {ex}"
            if clock_error is not None:
                error_details += f"; clock_source completion failure: {clock_error}"
            attempts_list.append(
                AttemptRecord(
                    attempt_index=attempt_idx,
                    started_at=started_at,
                    completed_at=completed_at,
                    adapter_status="UNKNOWN",
                    latency_ms=0.0,
                    error_code="ADAPTER_EXCEPTION",
                    error_details=error_details,
                    request_body_path=req_body_path,
                    request_body_sha256=req_body_sha256,
                    requested_model=req.model,
                )
            )
            technical_error_code = RunOutcomeCode.INTERNAL_RUNNER_ERROR
            break

        adapter_contract_error = validate_adapter_response_contract(resp)
        clock_error = None
        try:
            completed_at = sample_clock_timestamp(run_context.clock_source)
        except Exception as ex:
            completed_at = started_at
            clock_error = ex
        if adapter_contract_error is not None:
            attempts_list.append(
                AttemptRecord(
                    attempt_index=attempt_idx,
                    started_at=started_at,
                    completed_at=completed_at,
                    adapter_status="UNKNOWN",
                    latency_ms=0.0,
                    error_code="INVALID_ADAPTER_RESPONSE",
                    error_details=adapter_contract_error,
                    request_body_path=req_body_path,
                    request_body_sha256=req_body_sha256,
                    requested_model=req.model,
                )
            )
            technical_error_code = RunOutcomeCode.INTERNAL_RUNNER_ERROR
            break

        last_response = resp

        # Persistenza provider envelope (se presente)
        prov_env_path: Optional[str] = None
        prov_env_sha256: Optional[str] = None
        if resp.provider_envelope is not None:
            try:
                prov_env_path, prov_env_sha256 = store.save_provider_envelope(
                    run_id=run_context.run_id,
                    attempt_index=attempt_idx,
                    payload=resp.provider_envelope,
                )
            except Exception as ex:
                attempts_list.append(
                    AttemptRecord(
                        attempt_index=attempt_idx,
                        started_at=started_at,
                        completed_at=completed_at,
                        adapter_status=resp.status
                        if resp.status in VALID_ADAPTER_STATUSES
                        else "UNKNOWN",
                        latency_ms=resp.latency_ms,
                        token_usage=resp.token_usage,
                        error_code="INTERNAL_RUNNER_ERROR",
                        error_details=f"Fallimento persistenza provider envelope: {ex}",
                        request_body_path=req_body_path,
                        request_body_sha256=req_body_sha256,
                        http_status_code=resp.http_status_code,
                        requested_model=req.model,
                        returned_model=resp.returned_model,
                        requested_provider_order=tuple(req.routing_policy["provider_order"])
                        if req.routing_policy and "provider_order" in req.routing_policy
                        else None,
                        returned_provider=resp.returned_provider,
                        provider_response_id=resp.provider_response_id,
                    )
                )
                res = RunExecutionResult(
                    run_id=run_context.run_id,
                    configuration_ref=cfg_ref_str,
                    scenario_ref=scen_ref_str,
                    configuration_id=val_cfg.configuration_id,
                    scenario_id=canonical_scen.scenario_id,
                    final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
                    retry_policy=run_context.retry_policy,
                    attempts=tuple(attempts_list),
                    result_persisted=False,
                )
                return _finalize_and_persist_result(res, store)

        # Persistenza byte-exact del payload model output (se ricevuto)
        raw_path: Optional[str] = None
        raw_sha256: Optional[str] = None
        model_path: Optional[str] = None
        model_sha256: Optional[str] = None
        model_out_bytes = resp.get_model_output_bytes()
        if model_out_bytes is not None:
            try:
                raw_path, raw_sha256 = store.save_raw_payload(
                    run_id=run_context.run_id,
                    attempt_index=attempt_idx,
                    payload=model_out_bytes,
                )
                model_path, model_sha256 = store.save_model_output(
                    run_id=run_context.run_id,
                    attempt_index=attempt_idx,
                    payload=model_out_bytes,
                )
            except Exception as ex:
                rec_att = AttemptRecord(
                    attempt_index=attempt_idx,
                    started_at=started_at,
                    completed_at=completed_at,
                    adapter_status=resp.status if resp.status in VALID_ADAPTER_STATUSES else "UNKNOWN",
                    latency_ms=resp.latency_ms,
                    raw_response_path=None,
                    raw_response_sha256=None,
                    token_usage=resp.token_usage,
                    error_code="INTERNAL_RUNNER_ERROR",
                    error_details=f"Fallimento persistenza raw payload: {ex}",
                    request_body_path=req_body_path,
                    request_body_sha256=req_body_sha256,
                    provider_envelope_path=prov_env_path,
                    provider_envelope_sha256=prov_env_sha256,
                    model_output_path=None,
                    model_output_sha256=None,
                    http_status_code=resp.http_status_code,
                    requested_model=req.model,
                    returned_model=resp.returned_model,
                    requested_provider_order=tuple(req.routing_policy["provider_order"]) if req.routing_policy and "provider_order" in req.routing_policy else None,
                    returned_provider=resp.returned_provider,
                    provider_response_id=resp.provider_response_id,
                )
                attempts_list.append(rec_att)
                res = RunExecutionResult(
                    run_id=run_context.run_id,
                    configuration_ref=cfg_ref_str,
                    scenario_ref=scen_ref_str,
                    configuration_id=val_cfg.configuration_id,
                    scenario_id=canonical_scen.scenario_id,
                    final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
                    retry_policy=run_context.retry_policy,
                    attempts=tuple(attempts_list),
                    result_persisted=False,
                )
                return _finalize_and_persist_result(res, store)

        if clock_error is not None:
            attempts_list.append(
                AttemptRecord(
                    attempt_index=attempt_idx,
                    started_at=started_at,
                    completed_at=completed_at,
                    adapter_status=resp.status if resp.status in VALID_ADAPTER_STATUSES else "UNKNOWN",
                    latency_ms=resp.latency_ms,
                    raw_response_path=raw_path,
                    raw_response_sha256=raw_sha256,
                    token_usage=resp.token_usage,
                    error_code="CLOCK_SOURCE_ERROR",
                    error_details=f"Fallimento clock_source dopo invocazione adapter: {clock_error}",
                    request_body_path=req_body_path,
                    request_body_sha256=req_body_sha256,
                    provider_envelope_path=prov_env_path,
                    provider_envelope_sha256=prov_env_sha256,
                    model_output_path=model_path,
                    model_output_sha256=model_sha256,
                    http_status_code=resp.http_status_code,
                    requested_model=req.model,
                    returned_model=resp.returned_model,
                    requested_provider_order=tuple(req.routing_policy["provider_order"]) if req.routing_policy and "provider_order" in req.routing_policy else None,
                    returned_provider=resp.returned_provider,
                    provider_response_id=resp.provider_response_id,
                )
            )
            technical_error_code = RunOutcomeCode.INTERNAL_RUNNER_ERROR
            break

        # Controllo validita contrattuale dello status restituito dall'adapter
        if resp.status not in VALID_ADAPTER_STATUSES:
            technical_error_code = RunOutcomeCode.INTERNAL_RUNNER_ERROR
            rec_att = AttemptRecord(
                attempt_index=attempt_idx,
                started_at=started_at,
                completed_at=completed_at,
                adapter_status="UNKNOWN",
                latency_ms=resp.latency_ms,
                raw_response_path=raw_path,
                raw_response_sha256=raw_sha256,
                token_usage=resp.token_usage,
                error_code="INVALID_ADAPTER_STATUS",
                error_details=f"adapter_status non conforme '{resp.status}' (attesi: {sorted(VALID_ADAPTER_STATUSES)}); dettaglio: {resp.error_message}",
                request_body_path=req_body_path,
                request_body_sha256=req_body_sha256,
                provider_envelope_path=prov_env_path,
                provider_envelope_sha256=prov_env_sha256,
                model_output_path=model_path,
                model_output_sha256=model_sha256,
                http_status_code=resp.http_status_code,
                requested_model=req.model,
                returned_model=resp.returned_model,
                requested_provider_order=tuple(req.routing_policy["provider_order"]) if req.routing_policy and "provider_order" in req.routing_policy else None,
                returned_provider=resp.returned_provider,
                provider_response_id=resp.provider_response_id,
            )
            attempts_list.append(rec_att)
            break  # Violazione di contratto adapter: CATEGORICAMENTE nessun retry

        # Classificazione errore del tentativo conforme
        err_code_str: Optional[str] = None
        if resp.status != "SUCCESS":
            if resp.status == "TIMEOUT":
                err_code_str = "TIMEOUT"
                technical_error_code = RunOutcomeCode.TIMEOUT
            elif resp.status == "PROVIDER_ERROR":
                err_code_str = "PROVIDER_ERROR"
                technical_error_code = RunOutcomeCode.PROVIDER_ERROR
            else:
                err_code_str = "TRANSPORT_ERROR"
                technical_error_code = RunOutcomeCode.TRANSPORT_ERROR

        rec_att = AttemptRecord(
            attempt_index=attempt_idx,
            started_at=started_at,
            completed_at=completed_at,
            adapter_status=resp.status,
            latency_ms=resp.latency_ms,
            raw_response_path=raw_path,
            raw_response_sha256=raw_sha256,
            token_usage=resp.token_usage,
            error_code=err_code_str,
            error_details=resp.error_message,
            request_body_path=req_body_path,
            request_body_sha256=req_body_sha256,
            provider_envelope_path=prov_env_path,
            provider_envelope_sha256=prov_env_sha256,
            model_output_path=model_path,
            model_output_sha256=model_sha256,
            http_status_code=resp.http_status_code,
            requested_model=req.model,
            returned_model=resp.returned_model,
            requested_provider_order=tuple(req.routing_policy["provider_order"]) if req.routing_policy and "provider_order" in req.routing_policy else None,
            returned_provider=resp.returned_provider,
            provider_response_id=resp.provider_response_id,
        )
        attempts_list.append(rec_att)

        if resp.status == "SUCCESS":
            technical_error_code = None
            break

        # Controllo ammissibilita retry su errore tecnico transitorio
        is_transient = False
        if err_code_str in run_context.retry_policy.transient_errors:
            if err_code_str == "PROVIDER_ERROR":
                # Ammessi solo codici esplicitamente transitori (429 rate limit, 503 service unavailable)
                is_transient = resp.http_status_code in TRANSIENT_PROVIDER_HTTP_STATUS_CODES
            else:
                is_transient = True

        if is_transient and attempt_idx < max_attempts:
            continue
        else:
            break

    # Se l'ultimo tentativo e' un fallimento tecnico infrastrutturale
    if technical_error_code is not None:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id if val_cfg else None,
            scenario_id=canonical_scen.scenario_id if canonical_scen else None,
            final_outcome_code=technical_error_code,
            retry_policy=run_context.retry_policy,
            attempts=tuple(attempts_list),
            result_persisted=False,
        )
        return _finalize_and_persist_result(res, store)

    # 8. Parsing UTF-8 strict e JSON senza auto-riparazione
    payload_to_parse = last_response.get_model_output_bytes() if last_response else None
    parsed_candidate: Optional[Dict[str, Any]] = None
    malformed = False

    if payload_to_parse is None:
        malformed = True
    else:
        try:
            if isinstance(payload_to_parse, bytes):
                text_str = payload_to_parse.decode("utf-8")
            else:
                text_str = str(payload_to_parse)
            data = json.loads(
                text_str,
                parse_constant=reject_nonstandard_json_constant,
                object_pairs_hook=reject_duplicate_json_keys,
            )
            if isinstance(data, dict):
                parsed_candidate = data
            else:
                malformed = True
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            malformed = True

    if malformed or parsed_candidate is None:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.MALFORMED_RESPONSE,
            retry_policy=run_context.retry_policy,
            attempts=tuple(attempts_list),
            result_persisted=False,
        )
        return _finalize_and_persist_result(res, store)

    # 8.1 Validazione locale del payload decisionale esatto inviato dal provider.
    if model_generated_schema is not None:
        decision_errors = list(
            Draft202012Validator(
                model_generated_schema,
                format_checker=Draft202012Validator.FORMAT_CHECKER,
            ).iter_errors(parsed_candidate)
        )
        if decision_errors:
            res = RunExecutionResult(
                run_id=run_context.run_id,
                configuration_ref=cfg_ref_str,
                scenario_ref=scen_ref_str,
                configuration_id=val_cfg.configuration_id,
                scenario_id=canonical_scen.scenario_id,
                final_outcome_code=RunOutcomeCode.OUTPUT_SCHEMA_INVALID,
                retry_policy=run_context.retry_policy,
                attempts=tuple(attempts_list),
                parsed_candidate_output=freeze_data(parsed_candidate),
                result_persisted=False,
                preflight_errors=(
                    f"Violazione schema del payload decisionale: {decision_errors[0].message}",
                ),
            )
            return _finalize_and_persist_result(res, store)

    # 8.2 Assemblaggio dichiarato dei soli campi deterministici esclusi dal payload provider.
    if structured_output_contract is not None:
        try:
            parsed_candidate = assemble_canonical_turn_output(
                parsed_candidate,
                initial_state=projected_initial,
                scenario_id=canonical_scen.scenario_id,
                turn_timestamp=turn_timestamp,
                turn_index=canonical_scen.raw_data.get("initial_state", {}).get("turn_index", 1),
                experimental_factors=unfreeze_data(val_cfg.experimental_factors),
            )
        except (KeyError, TypeError, ValueError) as ex:
            res = RunExecutionResult(
                run_id=run_context.run_id,
                configuration_ref=cfg_ref_str,
                scenario_ref=scen_ref_str,
                configuration_id=val_cfg.configuration_id,
                scenario_id=canonical_scen.scenario_id,
                final_outcome_code=(RunOutcomeCode.OUTPUT_SEMANTICALLY_INVALID
                                    if isinstance(ex, AssemblyDecisionConflict)
                                    else RunOutcomeCode.OUTPUT_SCHEMA_INVALID),
                retry_policy=run_context.retry_policy,
                attempts=tuple(attempts_list),
                parsed_candidate_output=freeze_data(parsed_candidate),
                result_persisted=False,
                preflight_errors=(f"Assemblaggio deterministico fallito: {ex}",),
            )
            return _finalize_and_persist_result(res, store)

    # 9. Validazione schema strutturale agent_turn_output.schema.json
    schemas_dir = (repo_root / "schemas").resolve()
    try:
        turn_validator = _get_compiled_validator("agent_turn_output.schema.json", str(schemas_dir))
        turn_errors = list(turn_validator.iter_errors(parsed_candidate))
    except Exception as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=tuple(attempts_list),
            parsed_candidate_output=freeze_data(parsed_candidate),
            result_persisted=False,
            preflight_errors=(f"Errore interno validatore schema AgentTurnOutput: {ex}",),
        )
        return _finalize_and_persist_result(res, store)
    if turn_errors:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.OUTPUT_SCHEMA_INVALID,
            retry_policy=run_context.retry_policy,
            attempts=tuple(attempts_list),
            parsed_candidate_output=freeze_data(parsed_candidate),
            result_persisted=False,
            preflight_errors=tuple(f"Violazione schema: {e.message}" for e in turn_errors[:5]),
        )
        return _finalize_and_persist_result(res, store)

    # 10. Validazione semantica locale delle policy e stato di safety
    try:
        validate_agent_turn_output_semantics(
            agent_output=parsed_candidate,
            initial_state=projected_initial,
            validated_config=val_cfg,
            user_message=canonical_scen.user_message,
            turn_index=canonical_scen.raw_data.get("initial_state", {}).get("turn_index", 1),
            scenario_id=canonical_scen.scenario_id,
            repo_root=repo_root,
        )
    except ValueError as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.OUTPUT_SEMANTICALLY_INVALID,
            retry_policy=run_context.retry_policy,
            attempts=tuple(attempts_list),
            parsed_candidate_output=freeze_data(parsed_candidate),
            result_persisted=False,
            preflight_errors=(str(ex),),
        )
        return _finalize_and_persist_result(res, store)
    except Exception as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=tuple(attempts_list),
            parsed_candidate_output=freeze_data(parsed_candidate),
            result_persisted=False,
            preflight_errors=(f"Errore interno validazione semantica policy: {ex}",),
        )
        return _finalize_and_persist_result(res, store)

    # 11. Costruzione deterministica dell'ExecutionRunRecord
    try:
        final_state = derive_final_state(projected_initial, parsed_candidate, val_cfg)
    except Exception as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=tuple(attempts_list),
            parsed_candidate_output=freeze_data(parsed_candidate),
            result_persisted=False,
            preflight_errors=(f"Errore interno derivazione final_state: {ex}",),
        )
        return _finalize_and_persist_result(res, store)

    # Aggregazione token e latenza su tutti i tentativi
    tot_prompt_tok = sum(a.token_usage.prompt_tokens for a in attempts_list if a.token_usage is not None)
    tot_comp_tok = sum(a.token_usage.completion_tokens for a in attempts_list if a.token_usage is not None)
    tot_tokens = tot_prompt_tok + tot_comp_tok
    tot_latency = sum(a.latency_ms for a in attempts_list)

    # Determinazione stati terminali e di interazione
    safety_env = parsed_candidate.get("policy_outputs", {}).get("safety_envelope", {})
    decision = safety_env.get("decision")
    user_sig = safety_env.get("user_signal")
    auton_st = final_state["safety_state"].get("autonomy_status")
    bound_st = final_state["safety_state"].get("boundary_status")

    if decision == "STOP_AND_QUIESCE" or user_sig == "STOP":
        term_st = "STOPPED"
        inter_out = "STOPPED"
    elif (
        decision in ("RESPECT_REFUSAL", "ACCEPT_REVOCATION", "ACKNOWLEDGE_BOUNDARY")
        or auton_st in ("REFUSED", "REVOKED")
        or "OUT_OF_SCOPE" in str(bound_st)
    ):
        term_st = "REFUSED"
        inter_out = "REFUSED"
    else:
        term_st = "COMPLETED"
        inter_out = "SUPPORTED"

    record_dict: Dict[str, Any] = {
        "run_id": run_context.run_id,
        "timestamp": turn_timestamp,
        "configuration": {
            "configuration_id": val_cfg.configuration_id,
            "configuration_version": val_cfg.version,
            "prompt_fingerprint": composed_prompt.prompt_sha256,
            "artifact_fingerprint": composed_prompt.artifact_fingerprint,
            "module_hashes": dict(composed_prompt.module_hashes),
        },
        "runtime_profile": {
            "profile_id": val_cfg.runtime_profile["profile_id"],
            "version": val_cfg.runtime_profile["version"],
            "profile_fingerprint": hashlib.sha256(
                json.dumps(
                    {k: v for k, v in unfreeze_data(val_cfg.runtime_profile).items() if not k.startswith("$")},
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest(),
            "model": val_cfg.runtime_profile["model"],
            "provider": val_cfg.runtime_profile["provider"],
            "seed": val_cfg.runtime_profile["seed"],
            "max_tokens": val_cfg.runtime_profile["max_tokens"],
            "stop_sequences": list(val_cfg.runtime_profile.get("stop_sequences", [])),
        },
        "scenario": {
            "scenario_id": canonical_scen.scenario_id,
            "scenario_version": canonical_scen.version,
            "scenario_fingerprint": canonical_scen.scenario_fingerprint,
            "turn_index": canonical_scen.raw_data.get("initial_state", {}).get("turn_index", 1),
            "user_message": canonical_scen.user_message,
        },
        "initial_state": copy.deepcopy(projected_initial),
        "agent_output": copy.deepcopy(parsed_candidate),
        "final_state": copy.deepcopy(final_state),
        "execution_telemetry": {
            "latency_ms": tot_latency,
            "attempts": len(attempts_list),
            "token_usage": {
                "prompt_tokens": tot_prompt_tok,
                "completion_tokens": tot_comp_tok,
                "total_tokens": tot_tokens,
            },
            "errors": [],
        },
        "execution_status": "COMPLETED",
        "terminal_status": term_st,
        "interaction_outcome": inter_out,
        "safety_result": "PASS",
    }
    if "top_p" in val_cfg.runtime_profile:
        record_dict["runtime_profile"]["top_p"] = val_cfg.runtime_profile["top_p"]
    if "reasoning" in val_cfg.runtime_profile:
        record_dict["runtime_profile"]["reasoning"] = unfreeze_data(
            val_cfg.runtime_profile["reasoning"]
        )
    if "temperature" in val_cfg.runtime_profile:
        record_dict["runtime_profile"]["temperature"] = val_cfg.runtime_profile[
            "temperature"
        ]

    # 12. Validazione semantica del run record costruito
    try:
        validate_run_record_semantics(record_dict, val_cfg, repo_root=repo_root)
    except ValueError as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.RUN_RECORD_INVALID,
            retry_policy=run_context.retry_policy,
            attempts=tuple(attempts_list),
            parsed_candidate_output=freeze_data(parsed_candidate),
            result_persisted=False,
            preflight_errors=(f"Run record semantics validation failed: {ex}",),
        )
        return _finalize_and_persist_result(res, store)
    except Exception as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=tuple(attempts_list),
            parsed_candidate_output=freeze_data(parsed_candidate),
            result_persisted=False,
            preflight_errors=(f"Errore interno validatore semantico run record: {ex}",),
        )
        return _finalize_and_persist_result(res, store)

    # 13. Pubblicazione atomica del bundle run record + result ledger
    record_path = f"committed/{run_context.run_id}/record.json"
    result_path = f"committed/{run_context.run_id}/result.json"
    try:
        res_tmp = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.COMPLETED_VALID,
            retry_policy=run_context.retry_policy,
            attempts=tuple(attempts_list),
            protocol_version=run_context.protocol_version,
            execution_mode=run_context.execution_mode,
            replication_index=run_context.replication_index,
            batch_id=run_context.batch_id,
            parsed_candidate_output=freeze_data(parsed_candidate),
            run_record=freeze_data(record_dict),
            run_record_path=record_path,
            result_persisted=True,
            result_path=result_path,
        )
        saved_record_path, saved_result_path = store.save_completed_bundle(
            run_id=run_context.run_id,
            record_data=record_dict,
            result_data=res_tmp.to_dict(),
        )
        if saved_record_path != record_path or saved_result_path != result_path:
            raise RuntimeError("RunStore ha restituito percorsi bundle divergenti dal contratto")
        return res_tmp
    except Exception as ex:
        res = RunExecutionResult(
            run_id=run_context.run_id,
            configuration_ref=cfg_ref_str,
            scenario_ref=scen_ref_str,
            configuration_id=val_cfg.configuration_id,
            scenario_id=canonical_scen.scenario_id,
            final_outcome_code=RunOutcomeCode.INTERNAL_RUNNER_ERROR,
            retry_policy=run_context.retry_policy,
            attempts=tuple(attempts_list),
            parsed_candidate_output=freeze_data(parsed_candidate),
            run_record=None,
            run_record_path=None,
            result_persisted=False,
            result_path=None,
            preflight_errors=(f"Errore pubblicazione bundle record/ledger: {ex}",),
        )
        return res
