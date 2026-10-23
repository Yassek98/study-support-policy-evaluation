"""
Contesto di validazione: percorsi, caricamento schemi e istanziazione validator.
Permette di iniettare percorsi alternativi durante i test unitari.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


@dataclass(frozen=True)
class ValidationPaths:
    base: Path
    schemas: Path
    policies: Path
    scenarios: Path
    oracles: Path
    fixtures: Path
    benchmarks: Path
    development_scenarios: Path
    pol01_fixtures: Path
    pol02_fixtures: Path
    pol03_fixtures: Path
    pol04_fixtures: Path
    pol05_fixtures: Path
    configurations: Path
    prompts: Path
    prompt_modules: Path

    @classmethod
    def from_base(cls, base_dir: Path) -> "ValidationPaths":
        base = base_dir.resolve()
        schemas = base / "schemas"
        policies = base / "policies"
        scenarios = base / "scenarios"
        oracles = base / "oracle"
        fixtures = base / "fixtures"
        benchmarks = base / "benchmarks"
        return cls(
            base=base,
            schemas=schemas,
            policies=policies,
            scenarios=scenarios,
            oracles=oracles,
            fixtures=fixtures,
            benchmarks=benchmarks,
            development_scenarios=scenarios / "development",
            pol01_fixtures=fixtures / "pol01",
            pol02_fixtures=fixtures / "pol02",
            pol03_fixtures=fixtures / "pol03",
            pol04_fixtures=fixtures / "pol04",
            pol05_fixtures=fixtures / "pol05",
            configurations=base / "configurations",
            prompts=base / "prompts",
            prompt_modules=base / "prompts" / "modules",
        )


@dataclass
class ValidationContext:
    paths: ValidationPaths
    raw_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    registry: Registry = field(default_factory=Registry)
    validators: dict[str, jsonschema.Draft202012Validator] = field(default_factory=dict)

    def load_json(self, path: Path) -> dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def has_validator(self, schema_filename: str) -> bool:
        return schema_filename in self.validators

    def get_validator(self, schema_filename: str) -> jsonschema.Draft202012Validator | None:
        return self.validators.get(schema_filename)


def create_default_context(base_dir: Path | None = None) -> ValidationContext:
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent
    paths = ValidationPaths.from_base(base_dir)
    return ValidationContext(paths=paths)
