import pytest

from agents.api_agent_legacy_b import translate
from legacy_a.csv_reader import read_source
from legacy_b.schemas import LegacyBRecord
from legacy_b.simulator import generate, read_conflicts, to_mixed_case
from legacy_b.store import LegacyBStore
from rules.fields import dict_lookup
from tests.fixtures.make_csv import base_row, vin, write_csv

pytestmark = pytest.mark.integration
MODELS = ["CLIO", "MASTER", "KANGOO", "SCENIC E-TECH", "AR KANA", "MEGANE E-TECH"]


@pytest.fixture
def source(tmp_path):
    rows = [base_row(i, Vehmodel=MODELS[i % len(MODELS)]) for i in range(1, 41)]
    rows += [base_row(1, Vehmodel="#NOM?"), base_row(2, Vehmodel="MASTER"),   # VIN 2: two models
             base_row(50, Vehmodel=""), base_row(51, Vehmodel="Mégane E-Tech")]
    return read_source(write_csv(tmp_path / "a.csv", rows))


def test_same_seed_gives_byte_identical_files(tmp_path, source, cfg):
    a = generate(source, cfg, tmp_path / "g1")
    b = generate(source, cfg, tmp_path / "g2")
    assert a[0].read_bytes() == b[0].read_bytes() and a[1].read_bytes() == b[1].read_bytes()


def test_one_record_per_vin_and_planted_conflicts(tmp_path, source, cfg):
    rec, conf = generate(source, cfg, tmp_path / "g")
    store = LegacyBStore.from_csv(rec)
    vins = {vin(i) for i in list(range(1, 41)) + [50, 51]}
    assert len(store) == len(vins) and all(store.get(v) for v in vins)
    conflicts = read_conflicts(conf)
    assert len(conflicts) == 15 and len({c.vin for c in conflicts}) == 15
    assert vin(2) not in {c.vin for c in conflicts}                   # two models: not eligible
    assert vin(50) not in {c.vin for c in conflicts}                  # no model: not eligible
    for c in conflicts:
        b = dict_lookup(c.legacy_b_modele, cfg.vehicle_models)
        assert b is not None and b != c.legacy_a_model
        assert translate(store.get(c.vin), cfg).vehicle_model == b
    planted = {c.vin for c in conflicts}
    clean = vin(3) if vin(3) not in planted else vin(4)
    rows_model = dict_lookup(MODELS[int(clean[-2:]) % len(MODELS)], cfg.vehicle_models)
    assert translate(store.get(clean), cfg).vehicle_model == rows_model
    for line in rec.read_text(encoding="utf-8").splitlines()[1:]:
        modele = line.split(";")[1]
        assert any(ch.isupper() for ch in modele) and any(ch.islower() for ch in modele)


def test_to_mixed_case():
    assert to_mixed_case("MEGANE E-TECH") == "Megane E-Tech"
    assert to_mixed_case("SCENIC E-TECH") == "Scenic E-Tech"
    assert to_mixed_case("KANGOO") == "Kangoo"


def test_translation_examples_and_errors(cfg):
    t = translate(LegacyBRecord(CHASSIS_NO=vin(1).lower(), MODELE="Megane E-Tech", DATE_MAJ="20240315"), cfg)
    assert (t.vin, t.vehicle_model, t.updated_on, t.errors) == (vin(1), "MEGANE E-TECH", "2024-03-15", ())
    bad = translate(LegacyBRecord(CHASSIS_NO="SHORT", MODELE="Zoe", DATE_MAJ="20230230"), cfg)
    assert (bad.vin, bad.vehicle_model, bad.updated_on) == (None, None, None)
    assert [(e.field, e.raw) for e in bad.errors] == [("CHASSIS_NO", "SHORT"), ("MODELE", "Zoe"),
                                                      ("DATE_MAJ", "20230230")]
