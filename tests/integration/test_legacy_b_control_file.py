"""Requirement 30.11 on the real dataset: every row of every control-file VIN gets an R7 ASK_HUMAN."""
import pytest
from fastapi.testclient import TestClient

from agents.api_agent_legacy_b import ApiAgentLegacyB
from legacy_a.csv_reader import read_source
from legacy_b.api import create_app
from legacy_b.simulator import generate, read_conflicts
from legacy_b.store import LegacyBStore
from migration.pipeline import MigrationPipeline
from rules.fields import normalize_vin

pytestmark = [pytest.mark.integration, pytest.mark.dataset]


def test_control_file_vins_are_r7_ask_human(tmp_path, dataset_path, cfg):
    rec, conf = generate(read_source(dataset_path), cfg, tmp_path / "lb")
    conflicts = read_conflicts(conf)
    assert len(conflicts) == cfg.pipeline.legacy_b.conflicts
    agent = ApiAgentLegacyB(TestClient(create_app(LegacyBStore.from_csv(rec))), cfg)
    p = MigrationPipeline(dataset_path, var_dir=tmp_path / "var", legacy_b=agent)
    p.seed_reference_mapping()
    try:
        info = p.start_campaign()
        data = p.run_data(info.run_id)
        assert data.legacy_b_status == "enabled"
        planted = {c.vin for c in conflicts}
        rows = [r for r in data.rows if normalize_vin(r.values["VIN"]) in planted]
        assert rows
        for r in rows:
            dec = data.decisions[r.source_row_id]
            assert any(d.rule_id == "R7" and d.action == "ASK_HUMAN" and d.reason == "LEGACY_B_CONFLICT"
                       for d in dec.fields[:-1]), r.source_row_id
    finally:
        p.close()
