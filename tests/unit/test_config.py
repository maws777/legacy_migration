import shutil
from pathlib import Path

import pytest

from rules.config import CONFIG_FILES, DEFAULT_CONFIG_DIR, ConfigError, load_rule_config


@pytest.fixture
def cfg_dir(tmp_path: Path) -> Path:
    for name in CONFIG_FILES:
        shutil.copy(DEFAULT_CONFIG_DIR / name, tmp_path / name)
    return tmp_path


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_defaults_and_dictionaries():
    cfg = load_rule_config()
    p = cfg.pipeline
    assert p.confidence_threshold == 0.9
    assert p.batch_size == 1000
    assert p.llm.model == "qwen2.5:3b"
    assert p.llm.base_url == "http://127.0.0.1:11434"
    assert p.llm.timeout_s == 120
    assert p.llm.price_eur_per_input_token == 0.000001
    assert p.llm.price_eur_per_output_token == 0.000005
    assert p.discovery_sample_size == 20
    assert p.legacy_b.seed == 2026 and p.legacy_b.conflicts == 15
    assert p.control_sample.size == 50
    assert p.legacy_b_lookup is True
    assert set(p.resolver_auto_apply_fields) == {"originTool", "ecuName", "Vehmodel", "ecuCode"}
    assert "SCENIC E-TECH" in cfg.vehicle_models and len(cfg.vehicle_models) == 10
    assert len(cfg.ecus) == 12 and cfg.tools == ("DDT2000", "Clip", "CANalyzer")
    assert cfg.value_codes == ("ID0001", "ID0002", "ID0003", "ID0004", "ID0005")
    assert cfg.mappings.tool_map == {"DiagTool": "DDT2000", "AfterSalesDiag": "Clip"}
    assert cfg.column_mapping.canonical_for_target("DiagnosticOperation.diagnosticOpID") == "diagnosticFieId"
    assert len(cfg.config_hash) == 64


def test_threshold_defaults_to_0_9_when_absent(cfg_dir: Path):
    _edit(cfg_dir / "pipeline.yaml", "confidence_threshold: 0.9\n", "")
    assert load_rule_config(cfg_dir).pipeline.confidence_threshold == 0.9


def test_config_hash_changes_with_content(cfg_dir: Path):
    h1 = load_rule_config(cfg_dir).config_hash
    _edit(cfg_dir / "pipeline.yaml", "batch_size: 1000", "batch_size: 500")
    assert load_rule_config(cfg_dir).config_hash != h1


def test_missing_file_names_the_file(cfg_dir: Path):
    (cfg_dir / "mappings.yaml").unlink()
    with pytest.raises(ConfigError) as exc:
        load_rule_config(cfg_dir)
    assert exc.value.file == "mappings.yaml" and "not found" in exc.value.error


def test_invalid_yaml_names_the_file(cfg_dir: Path):
    (cfg_dir / "dictionaries.yaml").write_text("vehicle_models: [KANGOO\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_rule_config(cfg_dir)
    assert exc.value.file == "dictionaries.yaml" and "invalid YAML" in exc.value.error


@pytest.mark.parametrize("old,new", [
    ("confidence_threshold: 0.9", "confidence_threshold: 1.5"),
    ("confidence_threshold: 0.9", "confidence_threshold: -0.1"),
    ("batch_size: 1000", "batch_size: 0"),
    ("batch_size: 1000", "batch_size: 2.5"),
    ("batch_size: 1000", "batch_size: abc"),
])
def test_schema_errors_name_pipeline_yaml(cfg_dir: Path, old: str, new: str):
    _edit(cfg_dir / "pipeline.yaml", old, new)
    with pytest.raises(ConfigError) as exc:
        load_rule_config(cfg_dir)
    assert exc.value.file == "pipeline.yaml" and "schema error" in exc.value.error


def test_duplicate_normalized_key_is_refused(cfg_dir: Path):
    _edit(cfg_dir / "dictionaries.yaml", "  - KANGOO\n", "  - KANGOO\n  - KAN GOO\n")
    with pytest.raises(ConfigError) as exc:
        load_rule_config(cfg_dir)
    assert exc.value.file == "dictionaries.yaml" and "Normalized_Key" in exc.value.error


def test_tool_map_target_must_be_in_tools(cfg_dir: Path):
    _edit(cfg_dir / "mappings.yaml", "DiagTool: DDT2000", "DiagTool: DDT3000")
    with pytest.raises(ConfigError) as exc:
        load_rule_config(cfg_dir)
    assert exc.value.file == "mappings.yaml"


def test_top_level_must_be_mapping(cfg_dir: Path):
    (cfg_dir / "column_mapping.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_rule_config(cfg_dir)
    assert exc.value.file == "column_mapping.yaml"
