"""Rule_Config: YAML files under ``rules/config/`` validated with pydantic (design "Data Models")."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from rules.fields import normalized_key

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent / "config"
DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
_dotenv_loaded = False
CONFIG_FILES = ("dictionaries.yaml", "mappings.yaml", "column_mapping.yaml", "pipeline.yaml")


class ConfigError(Exception):
    """A Rule_Config file is missing, is not parseable YAML or fails schema validation."""

    def __init__(self, file: str, error: str) -> None:
        super().__init__(f"{file}: {error}")
        self.file = file
        self.error = error


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _distinct_keys(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for v in values:
        k = normalized_key(v)
        if not k:
            raise ValueError(f"{name}: entry {v!r} has an empty Normalized_Key")
        if k in seen:
            raise ValueError(f"{name}: entries {seen[k]!r} and {v!r} share the Normalized_Key {k!r}")
        seen[k] = v
    return values


class Dictionaries(_Frozen):
    vehicle_models: tuple[str, ...] = Field(min_length=1)
    ecus: tuple[str, ...] = Field(min_length=1)
    tools: tuple[str, ...] = Field(min_length=1)
    value_descriptions: dict[str, str] = Field(min_length=1)

    @field_validator("vehicle_models", "ecus", "tools")
    @classmethod
    def _check_distinct(cls, v: tuple[str, ...], info) -> tuple[str, ...]:
        return _distinct_keys(v, info.field_name)


class Mappings(_Frozen):
    tool_map: dict[str, str]
    did_map: dict[str, str]
    vin_labels: tuple[str, ...]


class ReferenceMapping(_Frozen):
    mapping: dict[str, str | None]          # source column -> target attribute
    required_targets: tuple[str, ...] = Field(min_length=1)

    def canonical_for_target(self, target_attribute: str) -> str:
        for column, target in self.mapping.items():
            if target == target_attribute:
                return column
        raise KeyError(target_attribute)


class LLMConfig(_Frozen):
    provider: Literal["anthropic", "ollama"] = "ollama"
    model: str = "qwen2.5:7b"
    base_url: str = "http://localhost:11434"            # Ollama only
    keep_alive: str = "2h"                              # Ollama only: keep the model loaded between calls
    timeout_s: float = Field(180, gt=0)
    # Extra tokens allowed on top of max_tokens for the hidden reasoning of "thinking" models (their reasoning
    # counts in num_predict: without headroom the answer comes back empty). Ollama only.
    reasoning_tokens: int = Field(0, ge=0)
    # Ollama `think` flag for reasoning models (qwen3, ...): false turns the reasoning off. None = model default.
    think: bool | None = None
    max_tokens: int = Field(150, ge=1)
    # Anthropic API prices: the real cost with provider anthropic, and the cloud-equivalent cost of any
    # provider (a local Ollama model costs 0 EUR; its tokens are still recorded and priced as if on the cloud).
    price_eur_per_input_token: float = Field(ge=0)
    price_eur_per_output_token: float = Field(ge=0)
    cloud_reference_model: str = "claude-haiku-4-5"     # model the two prices above belong to

    def price_per_token(self) -> tuple[float, float]:
        if self.provider == "ollama":
            return 0.0, 0.0
        return self.price_eur_per_input_token, self.price_eur_per_output_token


class ControlSampleConfig(_Frozen):
    size: int = Field(50, ge=1)
    seed: int = 42


class LegacyBSimConfig(_Frozen):
    seed: int = 2026
    conflicts: int = Field(15, ge=0)


class ServicesConfig(_Frozen):
    orchestrator: str = "http://127.0.0.1:8000"
    legacy_a: str = "http://127.0.0.1:8001"
    legacy_b: str = "http://127.0.0.1:8002"
    target: str = "http://127.0.0.1:8003"


class ClientAppConfig(_Frozen):
    interval_s: float = Field(1.0, ge=0.1, le=60)
    timeout_s: float = Field(5.0, gt=0)


class QueryAgentConfig(_Frozen):
    enabled: bool = False
    row_limit: int = Field(200, ge=1, le=1000)
    allowed_tables: tuple[str, ...] = (
        "vehicle", "diagnozed_ecu", "vehicle_ecu", "diag_tools_versions",
        "diagnostic_operation", "diag_value_result",
    )


class PathsConfig(_Frozen):
    source_csv: str = "data/legacy/hackathon-2026-digital-continuity-dataset.csv"
    target_db: str = "var/target.db"
    ops_db: str = "var/ops.db"
    checkpoints_db: str = "var/checkpoints.db"
    export_dir: str = "var/exports"


class PipelineConfig(_Frozen):
    confidence_threshold: float = Field(0.9, ge=0.0, le=1.0)
    batch_size: int = Field(1000, ge=1, strict=True)
    legacy_b_lookup: bool = True
    hitl_example_rows: int = Field(5, ge=1)
    discovery_sample_size: int = Field(20, ge=1)
    resolver_auto_apply_fields: tuple[str, ...] = ("originTool", "ecuName", "Vehmodel", "ecuCode")
    resolver_max_iterations: int = Field(3, ge=1, le=3)
    advisor_max_turns: int = Field(4, ge=1, le=4)            # Question_Advisor: LLM turns per consultation
    advisor_max_tokens: int = Field(400, ge=50)              # output tokens per Question_Advisor turn
    llm: LLMConfig
    control_sample: ControlSampleConfig = ControlSampleConfig()
    legacy_b: LegacyBSimConfig = LegacyBSimConfig()
    services: ServicesConfig = ServicesConfig()
    client_app: ClientAppConfig = ClientAppConfig()
    query_agent: QueryAgentConfig = QueryAgentConfig()
    paths: PathsConfig = PathsConfig()


class RuleConfig(_Frozen):
    dictionaries: Dictionaries
    mappings: Mappings
    column_mapping: ReferenceMapping
    pipeline: PipelineConfig
    config_hash: str

    @property
    def vehicle_models(self) -> tuple[str, ...]:
        return self.dictionaries.vehicle_models

    @property
    def ecus(self) -> tuple[str, ...]:
        return self.dictionaries.ecus

    @property
    def tools(self) -> tuple[str, ...]:
        return self.dictionaries.tools

    @property
    def value_codes(self) -> tuple[str, ...]:
        return tuple(sorted(self.dictionaries.value_descriptions))


_MODELS = {
    "dictionaries.yaml": Dictionaries,
    "mappings.yaml": Mappings,
    "column_mapping.yaml": ReferenceMapping,
    "pipeline.yaml": PipelineConfig,
}


def _read_yaml(path: Path) -> object:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ConfigError(path.name, "file not found") from None
    except OSError as exc:
        raise ConfigError(path.name, f"cannot read file: {exc.strerror or exc}") from None
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ConfigError(path.name, f"invalid YAML: {exc}") from None
    return {} if data is None else data


def load_dotenv() -> None:
    """Copy KEY=VALUE lines of the repository ``.env`` into the environment (once; real variables win).
    Set LEGACY_NO_DOTENV=1 to skip it (the test suite does)."""
    global _dotenv_loaded
    if _dotenv_loaded or os.environ.get("LEGACY_NO_DOTENV"):
        return
    _dotenv_loaded = True
    try:
        text = DOTENV_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _llm_with_env(llm: LLMConfig) -> LLMConfig:
    """Environment overrides for Ollama: OLLAMA_MODEL, OLLAMA_BASE_URL, OLLAMA_THINK, OLLAMA_REASONING_TOKENS (local server)."""
    if llm.provider != "ollama":
        return llm
    load_dotenv()
    update: dict[str, str] = {}
    if model := (os.environ.get("OLLAMA_MODEL") or "").strip():
        update["model"] = model
    if url := (os.environ.get("OLLAMA_BASE_URL") or "").strip():
        update["base_url"] = url
    think = (os.environ.get("OLLAMA_THINK") or "").strip().lower()
    if think in ("true", "1", "false", "0"):
        update["think"] = think in ("true", "1")
    reasoning = (os.environ.get("OLLAMA_REASONING_TOKENS") or "").strip()
    if reasoning.isdigit():
        return llm.model_copy(update={**update, "reasoning_tokens": int(reasoning)})
    return llm.model_copy(update=update) if update else llm


def load_rule_config(config_dir: Path | str = DEFAULT_CONFIG_DIR) -> RuleConfig:
    """Read and validate the four Rule_Config files. Raises ConfigError(file, error)."""
    config_dir = Path(config_dir)
    parsed: dict[str, BaseModel] = {}
    digest = hashlib.sha256()
    for name in CONFIG_FILES:
        path = config_dir / name
        data = _read_yaml(path)
        digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
        if not isinstance(data, dict):
            raise ConfigError(name, "schema error: top-level YAML value must be a mapping")
        try:
            parsed[name] = _MODELS[name].model_validate(data)
        except ValidationError as exc:
            raise ConfigError(name, f"schema error: {exc}") from None
    dico: Dictionaries = parsed["dictionaries.yaml"]  # type: ignore[assignment]
    maps: Mappings = parsed["mappings.yaml"]  # type: ignore[assignment]
    bad_tools = sorted(v for v in maps.tool_map.values() if v not in dico.tools)
    if bad_tools:
        raise ConfigError("mappings.yaml", f"schema error: tool_map targets not in tools: {bad_tools}")
    bad_codes = sorted(v for v in maps.did_map.values() if v not in dico.value_descriptions)
    if bad_codes:
        raise ConfigError("mappings.yaml", f"schema error: did_map targets not in value_descriptions: {bad_codes}")
    pipeline: PipelineConfig = parsed["pipeline.yaml"]  # type: ignore[assignment]
    pipeline = pipeline.model_copy(update={"llm": _llm_with_env(pipeline.llm)})
    return RuleConfig(
        dictionaries=dico,
        mappings=maps,
        column_mapping=parsed["column_mapping.yaml"],
        pipeline=pipeline,
        config_hash=digest.hexdigest(),
    )
