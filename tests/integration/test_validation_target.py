"""Validation_Target on the real 10,000-row file (Requirements 34.3, 34.4).

Empty Human_Decision_Store, Legacy_B_Lookup disabled. Deviations go to var/validation_deviation.json.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from migration.pipeline import MigrationPipeline
from rules.config import CONFIG_FILES, DEFAULT_CONFIG_DIR

pytestmark = [pytest.mark.integration, pytest.mark.dataset]
TARGET = {"MIGRATE": 6940, "CORRECT": 630, "REJECT": 1920, "ASK_HUMAN": 510, "OUT_OF_SCOPE": 990}
TOLERANCE = float(os.environ.get("VALIDATION_TOLERANCE", "0.05"))
OUT = Path(__file__).resolve().parents[2] / "var" / "validation_deviation.json"


def test_validation_target(tmp_path, dataset_path):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    for name in CONFIG_FILES:
        shutil.copy(DEFAULT_CONFIG_DIR / name, cfg_dir / name)
    text = (cfg_dir / "pipeline.yaml").read_text(encoding="utf-8")
    (cfg_dir / "pipeline.yaml").write_text(text.replace("legacy_b_lookup: true", "legacy_b_lookup: false"),
                                           encoding="utf-8")
    p = MigrationPipeline(dataset_path, var_dir=tmp_path / "var", config_dir=cfg_dir)
    p.seed_reference_mapping()
    try:
        info = p.start_campaign()
        assert p.hitl.decisions.all() == {}
        r = p.reports.build(info.run_id)
        measured = {"MIGRATE": r.migrate, "CORRECT": r.correct, "REJECT": r.reject, "ASK_HUMAN": r.ask_human,
                    "OUT_OF_SCOPE": r.out_of_scope}
        assert r.analysed == 10000 and r.migrate + r.correct + r.reject + r.ask_human == 10000
        assert p.metrics.get(info.run_id).legacy_b_lookup == "disabled"
        deviations = {k: {"measured": v, "target": TARGET[k], "difference": v - TARGET[k]}
                      for k, v in measured.items() if v != TARGET[k]}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"run_id": info.run_id, "tolerance": TOLERANCE, "measured": measured,
                                   "deviations": deviations}, indent=2), encoding="utf-8")
        for k, v in measured.items():
            assert abs(v - TARGET[k]) <= TOLERANCE * TARGET[k], (k, v, TARGET[k])
    finally:
        p.close()
