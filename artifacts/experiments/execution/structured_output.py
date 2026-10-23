"""
Modulo per la gestione deterministica, specializzazione e bundling di Structured Outputs.
Risolve i $ref locali di agent_turn_output.schema.json in schemi specializzati per configurazione
e li compila nel formato rigidamente conforme a OpenRouter / OpenAI response_format (strict: true).
"""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple, Union


def canonical_json_dumps(data: Any) -> str:
    """Serializza dati in JSON canonico deterministico (RFC 8785 subset)."""
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def compute_sha256(data: Union[str, bytes]) -> str:
    """Calcola l'hash SHA-256 esadecimale di stringhe o byte."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _infer_enum_type(values: Any) -> Union[str, list[str]]:
    """Inferisce un tipo JSON esplicito per enum strict privi di ``type``."""
    if not isinstance(values, list) or not values:
        raise ValueError("enum strict vuoto o non valido")

    def json_type(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, str):
            return "string"
        raise ValueError(f"tipo enum non supportato nello schema strict: {type(value).__name__}")

    inferred = list(dict.fromkeys(json_type(value) for value in values))
    if len(inferred) == 1:
        return inferred[0]
    if len(inferred) == 2 and "null" in inferred:
        return inferred
    raise ValueError(f"enum strict con tipi eterogenei non supportati: {inferred!r}")


def _normalize_def_name(schema_filename: str) -> str:
    """Estrae un nome canonico per $defs dal nome file dello schema."""
    name = Path(schema_filename).name
    if name.endswith(".schema.json"):
        name = name[:-12]
    elif name.endswith(".json"):
        name = name[:-5]
    return name.replace("-", "_").replace(".", "_")


def bundle_schema_with_defs(
    root_schema_filename: str = "agent_turn_output.schema.json",
    schemas_dir: Optional[Union[str, Path]] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """
    Carica lo schema radice e risolve ricorsivamente tutti i $ref locali in un unico
    schema standalone con sezione $defs, verificando che non vi siano riferimenti esterni.

    Returns:
        Tuple[Dict[str, Any], str, str]:
            (bundled_schema, source_schema_sha256, bundled_schema_sha256)
    """
    if schemas_dir is None:
        schemas_dir = Path(__file__).resolve().parent.parent / "schemas"
    else:
        schemas_dir = Path(schemas_dir).resolve()

    root_path = (schemas_dir / root_schema_filename).resolve()
    if not root_path.is_file():
        raise FileNotFoundError(f"Schema radice non trovato: {root_path}")

    # Verifica sandboxing rispetto a schemas_dir
    try:
        root_path.relative_to(schemas_dir)
    except ValueError:
        raise ValueError(f"Violazione di confinamento dello schema radice: {root_path}")

    source_content = root_path.read_text(encoding="utf-8")
    source_sha256 = compute_sha256(source_content)
    root_schema = json.loads(source_content)

    defs: Dict[str, Any] = {}
    loaded_files: Set[str] = set()

    def load_subschema(ref_target: str) -> str:
        # Respinge URI esterni o schemi remoti non locali
        if ref_target.startswith("http://") or ref_target.startswith("https://"):
            raise ValueError(f"Riferimento $ref esterno non consentito: {ref_target!r}")
        if ref_target.startswith("#"):
            # Riferimento interno già risolto
            return ref_target

        ref_clean = ref_target.split("#")[0]
        target_path = (schemas_dir / ref_clean).resolve()
        try:
            target_path.relative_to(schemas_dir)
        except ValueError:
            raise ValueError(f"Violazione di confinamento per $ref: {ref_target!r} -> {target_path}")

        if not target_path.is_file():
            raise FileNotFoundError(f"Sub-schema referenziato non trovato: {target_path}")

        def_key = _normalize_def_name(target_path.name)
        if target_path.name not in loaded_files:
            loaded_files.add(target_path.name)
            sub_raw = json.loads(target_path.read_text(encoding="utf-8"))
            # Rimuove $schema e $id dai sub-schemi inseriti in $defs
            sub_raw.pop("$schema", None)
            sub_raw.pop("$id", None)
            # Trasforma ricorsivamente i $ref nel subschema
            transformed = transform_refs(sub_raw)
            defs[def_key] = transformed

        return f"#/$defs/{def_key}"

    def transform_refs(node: Any) -> Any:
        if isinstance(node, dict):
            new_dict = {}
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    new_dict[k] = load_subschema(v)
                else:
                    new_dict[k] = transform_refs(v)
            return new_dict
        elif isinstance(node, list):
            return [transform_refs(item) for item in node]
        return copy.deepcopy(node)

    bundled = transform_refs(root_schema)
    if defs:
        existing_defs = bundled.get("$defs", {})
        existing_defs.update(defs)
        bundled["$defs"] = existing_defs

    bundled_json_canonical = canonical_json_dumps(bundled)
    bundled_sha256 = compute_sha256(bundled_json_canonical)

    return bundled, source_sha256, bundled_sha256


def specialize_agent_turn_output_schema(
    bundled_schema: Dict[str, Any],
    experimental_factors: Dict[str, bool],
) -> Dict[str, Any]:
    r"""
    Specializza lo schema radice bundled agent_turn_output in base ai fattori sperimentali
    della configurazione attiva ($B_0 \dots B_1$).
    - Rimuove tassativamente dalle properties di policy_outputs le policy non attive;
    - Imposta policy_outputs.required = ["safety_envelope", *active_keys];
    - Limita l'enum decision_trace.source alle sole policy autorizzate.
    """
    expected_factors = {"P1_POL01", "P2_POL02", "P3_POL03"}
    if set(experimental_factors) != expected_factors or not all(
        isinstance(value, bool) for value in experimental_factors.values()
    ):
        raise ValueError("experimental_factors non canonici per specializzazione schema")

    specialized = copy.deepcopy(bundled_schema)
    root_properties = specialized.get("properties")
    if not isinstance(root_properties, dict):
        raise ValueError("schema agent_turn_output privo di properties radice")

    policy_outputs = root_properties.get("policy_outputs")
    if not isinstance(policy_outputs, dict):
        raise ValueError("schema agent_turn_output privo di policy_outputs")
    policy_properties = policy_outputs.get("properties")
    if not isinstance(policy_properties, dict) or "safety_envelope" not in policy_properties:
        raise ValueError("schema policy_outputs privo del safety envelope")

    active_bindings = (
        ("P1_POL01", "pol01", "POL-01"),
        ("P2_POL02", "pol02", "POL-02"),
        ("P3_POL03", "pol03", "POL-03"),
    )
    active_keys = [
        output_key
        for factor_key, output_key, _ in active_bindings
        if experimental_factors[factor_key]
    ]
    active_sources = [
        source
        for factor_key, _, source in active_bindings
        if experimental_factors[factor_key]
    ]

    for _, output_key, _ in active_bindings:
        if output_key not in active_keys:
            policy_properties.pop(output_key, None)
    policy_outputs["required"] = ["safety_envelope", *active_keys]

    top_trace = root_properties.get("decision_trace")
    if not isinstance(top_trace, dict) or "items" not in top_trace:
        raise ValueError("schema agent_turn_output privo di decision_trace.items")

    # Sovrascrive gli items del decision_trace di primo livello con enum vincolato
    top_trace["items"] = {
        "allOf": [
            {"$ref": "#/$defs/trace_event"},
            {
                "type": "object",
                "properties": {
                    "source": {
                        "enum": [
                            "CORE",
                            "SAFETY",
                            "POL-04",
                            "POL-05",
                            *active_sources,
                        ]
                    }
                },
            },
        ]
    }
    return specialized


def project_model_generated_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Rimuove dal contratto provider i campi assemblati deterministicamente dal runner."""
    projected = copy.deepcopy(schema)

    def remove_fields(target: Dict[str, Any], fields: Set[str]) -> None:
        properties = target.get("properties")
        if not isinstance(properties, dict):
            raise ValueError("schema proiettato privo di properties")
        for field in fields:
            properties.pop(field, None)
        required = target.get("required")
        if isinstance(required, list):
            target["required"] = [field for field in required if field not in fields]

    def references_any_field(node: Any, fields: Set[str]) -> bool:
        if isinstance(node, dict):
            return any(
                key in fields or references_any_field(value, fields)
                for key, value in node.items()
            )
        if isinstance(node, list):
            return any(references_any_field(value, fields) for value in node)
        return False

    def remove_runner_owned_conditionals(target: Dict[str, Any], fields: Set[str]) -> None:
        all_of = target.get("allOf")
        if not isinstance(all_of, list):
            return
        retained = [branch for branch in all_of if not references_any_field(branch, fields)]
        if retained:
            target["allOf"] = retained
        else:
            target.pop("allOf", None)

    remove_fields(projected, {"safety_state"})
    defs = projected.get("$defs")
    if not isinstance(defs, dict):
        raise ValueError("schema bundled privo di $defs")

    common = {"scenario_id", "policy_id", "policy_version", "timestamp", "system_response"}
    for definition_name in (
        "safety_envelope_output",
        "pol01_output",
        "pol02_output",
        "pol03_output",
    ):
        definition = defs.get(definition_name)
        if not isinstance(definition, dict):
            raise ValueError(f"definizione mancante: {definition_name}")
        remove_fields(definition, common)

    safety_runner_owned = {
        "safety_state_before",
        "safety_state_after",
        "support_action_allowed",
    }
    pol02_runner_owned = {"configuration_context", "level_before", "scaffold_state"}
    pol03_runner_owned = {"action_count", "pol01_handoff_ready"}
    remove_fields(defs["safety_envelope_output"], safety_runner_owned)
    remove_runner_owned_conditionals(defs["safety_envelope_output"], safety_runner_owned)
    remove_fields(defs["pol02_output"], pol02_runner_owned)
    remove_runner_owned_conditionals(defs["pol02_output"], pol02_runner_owned)
    remove_fields(defs["pol03_output"], pol03_runner_owned)
    remove_runner_owned_conditionals(defs["pol03_output"], pol03_runner_owned)
    remove_fields(
        defs["action_record"],
        {
            "action_version",
            "action_status",
            "accepted_version",
            "explicit_user_evidence",
            "grounded_facts",
            "created_at_turn",
            "updated_at_turn",
        },
    )
    # The canonical action-record conditionals are keyed by runner-owned fields
    # (notably action_status) and also constrain runner-owned values.  Keeping
    # those branches in the provider projection makes an absent discriminator
    # satisfy JSON Schema's property-only `if`, which can spuriously require
    # fields such as accepted_version.  The unchanged canonical schema is
    # applied after deterministic assembly, so these conditions belong only to
    # that post-assembly validation boundary.
    defs["action_record"].pop("allOf", None)
    return projected


def compile_openai_strict_schema(schema: Any, defs: Optional[Dict[str, Any]] = None) -> Any:
    """
    Compila ricorsivamente uno schema JSON Draft 2020-12 nel sottoinsieme
    accettato dagli Structured Outputs OpenAI instradati tramite OpenRouter.

    Funzionalità chiave:
    - Risolve le clausole 'allOf' fondendo $ref e override di proprietà (es. enum vincolati) in schemi inline;
    - Assicura che i nodi '$ref' siano oggetti singoli senza chiavi sorelle;
    - Converte 'const: v' in 'type: ..., enum: [v]';
    - Assegna 'additionalProperties: false' e 'required: list(properties.keys())' a tutti gli oggetti;
    - Rimuove metadati e costrutti di composizione non accettati dal compilatore;
    - Mantiene descrizioni e vincoli semplici supportati, utili anche al modello;
    - Lascia la validazione locale completa come autorita' semantica finale.
    """
    if not isinstance(schema, dict):
        return schema

    if defs is None:
        defs = schema.get("$defs", {})

    # Le condizioni Draft 2020-12 restano autoritative nei validatori locali, ma non
    # appartengono al sottoinsieme Structured Outputs. In tal caso si conserva lo
    # schema base e si rimuove soltanto la clausola condizionale.
    if "allOf" in schema:
        branches = schema["allOf"]
        if any(
            isinstance(branch, dict)
            and bool({"if", "then", "else", "not"}.intersection(branch))
            for branch in branches
        ):
            base_without_conditions = {
                key: value for key, value in schema.items() if key != "allOf"
            }
            return compile_openai_strict_schema(base_without_conditions, defs)

        # Risoluzione inline per allOf non condizionali, usato dalla
        # specializzazione della source nel decision trace.
        base_without_all_of = {
            key: value for key, value in schema.items() if key != "allOf"
        }
        compiled_base = compile_openai_strict_schema(base_without_all_of, defs)
        merged: Dict[str, Any] = (
            compiled_base if isinstance(compiled_base, dict) else {}
        )
        for branch in branches:
            if isinstance(branch, dict) and "$ref" in branch:
                ref_name = branch["$ref"].split("/")[-1]
                if ref_name in defs:
                    base_compiled = compile_openai_strict_schema(copy.deepcopy(defs[ref_name]), defs)
                    if isinstance(base_compiled, dict):
                        for k, v in base_compiled.items():
                            if k != "$ref":
                                if k == "properties" and "properties" in merged:
                                    merged["properties"].update(v)
                                else:
                                    merged[k] = v
            elif isinstance(branch, dict):
                branch_compiled = compile_openai_strict_schema(branch, defs)
                if isinstance(branch_compiled, dict):
                    for k, v in branch_compiled.items():
                        if k != "$ref":
                            if k == "properties" and "properties" in merged:
                                merged["properties"].update(v)
                            else:
                                merged[k] = v
        merged.pop("allOf", None)
        merged.pop("$ref", None)
        return compile_openai_strict_schema(merged, defs)

    # Nodo $ref puro isolato
    if "$ref" in schema:
        return {"$ref": schema["$ref"]}

    res: Dict[str, Any] = {}
    for k, v in schema.items():
        if k in (
            "$schema",
            "$id",
            "allOf",
            "if",
            "then",
            "else",
            "title",
            "default",
            "examples",
            "readOnly",
            "writeOnly",
            "uniqueItems",
        ):
            continue
        if k == "const":
            res["enum"] = [v]
            if isinstance(v, str):
                res["type"] = "string"
            elif isinstance(v, bool):
                res["type"] = "boolean"
            elif isinstance(v, int):
                res["type"] = "integer"
            elif isinstance(v, float):
                res["type"] = "number"
            continue
        if k == "$defs" and isinstance(v, dict):
            res[k] = {def_name: compile_openai_strict_schema(def_val, defs) for def_name, def_val in v.items()}
            continue
        if isinstance(v, dict):
            res[k] = compile_openai_strict_schema(v, defs)
        elif isinstance(v, list):
            res[k] = [compile_openai_strict_schema(x, defs) if isinstance(x, dict) else x for x in v]
        else:
            res[k] = v

    # OpenAI richiede un tipo esplicito anche per i nodi definiti soltanto da enum.
    # Draft 2020-12 lo rende opzionale, quindi il compilatore deve aggiungerlo.
    if "enum" in res and "type" not in res:
        res["type"] = _infer_enum_type(res["enum"])

    if res.get("type") == "object" and "properties" in res:
        res["additionalProperties"] = False
        res["required"] = list(res["properties"].keys())

    return res


def build_openrouter_structured_output_contract(
    bundled_schema: Dict[str, Any],
    schema_name: str = "agent_turn_output",
    strict: bool = True,
) -> Dict[str, Any]:
    """
    Costruisce l'oggetto response_format strutturato per l'API di OpenRouter / OpenAI.
    """
    final_schema = compile_openai_strict_schema(bundled_schema) if strict else copy.deepcopy(bundled_schema)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "strict": strict,
            "schema": final_schema,
        },
    }
