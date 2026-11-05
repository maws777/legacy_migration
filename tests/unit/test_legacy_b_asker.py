"""Legacy B agent asking the Query_Agent its own questions (scripted LLM, fake chat)."""
import json

from agents.legacy_b_asker import LegacyBAsker, fallback_questions, main
from agents.llm_client import LLMFailure
from agents.schemas import ChatResponse
from legacy_b.schemas import LegacyBRecord
from legacy_b.store import LegacyBStore
from rules.errors import ErrorBody
from tests.fixtures.fake_llm import FakeLLM

VINS = [f"VF1AB000000000000{i:03d}" for i in range(3)]
STORE = LegacyBStore([LegacyBRecord(CHASSIS_NO=VINS[0], MODELE="Clio", DATE_MAJ="20240101"),
                      LegacyBRecord(CHASSIS_NO=VINS[1], MODELE="Megane E-Tech", DATE_MAJ="20240102")])
QS = ["Which VINs have a model different from mine?", "Which vehicles are unknown to me?", "Latest diagnostics?"]


def response(rows=(), error=None, sql="SELECT vin, vehicle_model FROM vehicle"):
    return ChatResponse(sql=sql, rows=list(rows), columns=list(rows[0]) if rows else [], truncated=False,
                        input_tokens=500, output_tokens=40, error=error, seconds=2.5)


def test_questions_come_from_the_llm_and_are_metered(cfg):
    llm = FakeLLM(lambda body: json.dumps({"questions": QS}))
    qs, origin, tokens = LegacyBAsker(llm, None, STORE, cfg).questions()
    assert (qs, origin, tokens) == (QS, "llm", 120)
    assert [(c["run_id"], c["purpose"]) for c in llm.calls] == [("legacy-b", "legacy_b_query")]
    info = json.loads(llm.calls[0]["prompt"])["legacy_b_data"]
    assert info["records"] == 2 and info["models"] == {"CLIO": 1, "MEGANE E-TECH": 1}


def test_fallback_questions(cfg):
    for llm, why in ((None, "no LLM"), (FakeLLM(available=False), "no LLM"),
                     (FakeLLM(lambda b: json.dumps({"questions": ["only one"]})), "not 3 questions"),
                     (FakeLLM(lambda b: LLMFailure("timeout", "ReadTimeout")), "LLM failure timeout")):
        qs, origin, _ = LegacyBAsker(llm, None, STORE, cfg).questions()
        assert origin.startswith("fallback") and why in origin and len(qs) == 3
    assert VINS[0] in fallback_questions({"sample_vins": [VINS[0]]})[1]


def test_compare_with_own_records(cfg):
    asker = LegacyBAsker(None, None, STORE, cfg)
    rows = [{"vin": VINS[0], "vehicle_model": "CLIO"}, {"vin": VINS[1], "vehicle_model": "CAPTUR"},
            {"vin": VINS[2], "vehicle_model": "MASTER"}]
    assert asker.compare(response(rows)) == ("compared with Legacy B: 1 same model, 1 different, "
                                             "1 VIN(s) unknown to Legacy B")
    assert asker.compare(response([{"vehicle_model": "CLIO", "n": 2}])) is None
    assert asker.compare(response(error=ErrorBody(code="REFUSED", message="validate_sql: x"))) is None


def test_run_asks_the_three_questions_through_the_chat(cfg):
    asked, lines = [], []

    def chat(q):
        asked.append(q)
        if len(asked) == 2:
            return response(error=ErrorBody(code="REFUSED", message="validate_sql: non-SELECT statement"))
        return response([{"vin": VINS[0], "vehicle_model": "CLIO"}])

    results = LegacyBAsker(FakeLLM(lambda b: json.dumps({"questions": QS})), chat, STORE, cfg).run(lines.append)
    assert asked == QS and len(results) == 3
    text = "\n".join(lines)
    assert "3 questions (llm, 120 tokens)" in text and "error: validate_sql: non-SELECT statement" in text
    assert "1 same model, 0 different" in text and "540 tokens, 2.5 s" in text


def test_main_reports_an_unreachable_orchestrator(tmp_path, capsys):
    records = tmp_path / "b.csv"
    records.write_text(f"CHASSIS_NO;MODELE;DATE_MAJ\n{VINS[0]};Clio;20240101\n", encoding="utf-8")
    assert main(["--url", "http://127.0.0.1:9", "--records", str(records), "--var", str(tmp_path), "--no-llm"]) == 1
    assert "Orchestrator unreachable" in capsys.readouterr().err
