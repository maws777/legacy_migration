import json
from pathlib import Path

import pytest

from agents.api_agent_legacy import MappingConflictError
from agents.llm_client import LLMFailure
from agents.resolver_agent import ResolverAgent, build_prompt, parse_answer
from hitl.schemas import HumanAnswer
from migration.pipeline import MigrationPipeline
from rules.types import AllowedValues, UnknownVariant
from tests.fixtures.fake_llm import FakeLLM, answer
from tests.fixtures.make_csv import HEADER, base_row, write_csv

EDGE = Path(__file__).resolve().parents[2] / "data" / "synthetic_edge_cases.csv"
ECU_UV = UnknownVariant("ecuName|UNKNOWN_VARIANT|AIRBAGUNIT", "ecuName", "AIRBAGUNIT",
                        AllowedValues(values=("BCM", "AIRBAG")))
DATE_UV = UnknownVariant("Creation_date|UNKNOWN_VARIANT|x", "Creation_date", "2024-03-15 10:30",
                         AllowedValues(format="dd/mm/yyyy hh:mm"))

ANSWERS = {
    "MÉGANEETECH": answer("map", "MEGANE E-TECH", 0.97),
    "CLIP": answer("map", "Clip", 0.95),
    "Diag Tool": answer("map", "DDT2000", 0.6, "probably the DiagTool alias"),
    "AIRBAGUNIT": answer("map", "AIRBAG", 0.93),
    "2024-03-15 10:30": answer("map", "15/03/2024 10:30", 0.99),
    "ID 0004": "this is not json",
}


# ---------------------------------------------------------------- resolver prompt and parsing
def test_prompt_contains_only_field_reason_value_allowed():
    body = json.loads(build_prompt(ECU_UV))
    assert body == {"field": "ecuName", "reason": "UNKNOWN_VARIANT", "value": "AIRBAGUNIT", "allowed": ["BCM", "AIRBAG"]}
    assert json.loads(build_prompt(DATE_UV))["allowed_format"] == "dd/mm/yyyy hh:mm"


@pytest.mark.parametrize("text,conf", [
    (answer("map", "AIRBAG", 0.93), 0.93),
    ('noise {"decision":"map","value":"AIRBAG","confidence":0.8,"justification":"x"} tail', 0.8),
    ("not json", 0.0),
    (answer("map", "ADAS", 0.99), 0.0),                     # outside the allowed values
    (answer("map", "AIRBAG", 1.5), 0.0),
    (answer("map", "AIRBAG", 0.9, ""), 0.0),
    ('{"decision":"map","confidence":0.9,"justification":"x"}', 0.0),
    (answer("reject", None, 0.95), 0.95),
])
def test_parse_answer(text, conf):
    s = parse_answer(text, ECU_UV)
    assert s.confidence == conf and s.justification


def test_date_value_must_match_the_format():
    assert parse_answer(answer("map", "15/03/2024 10:30", 0.9), DATE_UV).confidence == 0.9
    assert parse_answer(answer("map", "2024-03-15", 0.9), DATE_UV).confidence == 0.0


def test_failure_gives_confidence_zero():
    llm = FakeLLM(lambda body: LLMFailure("timeout", "APITimeoutError"))
    s, tokens = ResolverAgent(llm).propose("r", ECU_UV)
    assert s.confidence == 0 and "timeout" in s.justification and tokens == 0


# ---------------------------------------------------------------- pipeline with the resolver
@pytest.fixture
def edge(tmp_path):
    def make(llm, var="var"):
        p = MigrationPipeline(EDGE, var_dir=tmp_path / var, llm=llm)
        p.seed_reference_mapping()
        return p
    return make


def test_one_call_per_pattern_key_and_threshold_gate(edge):
    llm = FakeLLM(ANSWERS)
    p = edge(llm)
    info = p.start_campaign()
    calls = [c for c in llm.calls if c["purpose"] == "resolver"]
    assert len(calls) == 6
    assert len({c["prompt"] for c in calls}) == 6                   # rows 2 and 6 share "CLIP"
    decs = p.run_data(info.run_id).decisions
    assert decs[1].target.vehicle_model == "MEGANE E-TECH" and decs[1].outcome == "CORRECT"
    tool = [d for d in decs[2].fields if d.field == "originTool"][0]
    assert (tool.decided_by, tool.reason, tool.confidence, tool.tokens_used) == ("llm", "LLM_VALUE", 0.95, 120)
    assert decs[6].target.diag_tool_name == "Clip"
    assert decs[3].outcome == "ASK_HUMAN" and decs[7].outcome == "ASK_HUMAN"
    assert decs[5].outcome == "ASK_HUMAN"                           # dates are never auto-applied
    qs = {q.pattern_key: q for q in p.hitl.open_questions(info.run_id)}
    q = qs["originTool|UNKNOWN_VARIANT|Diag Tool"]
    assert q.llm_status == "proposed_below_threshold" and q.llm_suggestion.value == "DDT2000"
    assert qs["Creation_date|UNKNOWN_VARIANT|2024-03-15 10:30"].llm_status == "proposed_human_only"
    assert qs["ecuCode|UNKNOWN_VARIANT|ID 0004"].llm_status == "failed"
    m = p.metrics.get(info.run_id)
    # 6 Resolver_Agent calls of the Run + the schema discovery made before it (attributed to the first Run).
    assert (m.llm_calls, m.input_tokens, m.output_tokens, m.cache_hit_rate) == (7, 700, 140, 0.0)
    cost = p.cost_breakdown(info.run_id)
    assert {u.purpose: (u.calls, u.tokens) for u in cost.by_purpose if u.calls} == {
        "resolver": (6, 720), "schema_discovery": (1, 120)}
    assert p.reports.build(info.run_id).tokens == 840 and p.cache.count() == 3
    p.close()


def test_cache_reuse_with_zero_tokens_and_human_first(edge):
    llm = FakeLLM(ANSWERS)
    p = edge(llm)
    first = p.start_campaign()
    q = next(x for x in p.hitl.open_questions(first.run_id) if x.pattern_key == "originTool|UNKNOWN_VARIANT|Diag Tool")
    p.hitl.answer(q.question_id, HumanAnswer(option="apply_value", value="DDT2000", operator="h"))
    p.hitl.decisions.put(p.hitl.decisions.get(q.pattern_key).model_copy(
        update={"pattern_key": "ecuName|UNKNOWN_VARIANT|AIRBAGUNIT", "kind": "reject", "value": None}))
    n = len(llm.calls)
    second = p.resume(first.campaign_id, "h")
    assert len(llm.calls) - n == 2                                     # only the uncached, undecided keys
    decs = p.run_data(second.run_id).decisions
    tool = [d for d in decs[2].fields if d.field == "originTool"][0]
    assert (tool.reason, tool.tokens_used, tool.decided_by) == ("CACHE_VALUE", 0, "llm")
    ecu = [d for d in decs[4].fields if d.field == "ecuName"][0]
    assert (ecu.action, ecu.decided_by) == ("REJECT", "human")        # human beats the cache
    assert decs[3].outcome == "CORRECT"
    m = p.metrics.get(second.run_id)
    assert m.cache_hit_rate == pytest.approx(2 / 4)
    assert p.reports.build(second.run_id).cache_state == "warm"
    p.close()


# ---------------------------------------------------------------- schema discovery
def _discovery_answer(mapping: dict) -> str:
    return json.dumps({"column_mapping": mapping, "formats": [{"name": "Format_1", "ecuCode_pattern": "^ID000[1-5]$"},
                       {"name": "Format_2", "ecuCode_pattern": "^Id[0-9]{3}$"}], "format_criterion": "ecuCode"})


def test_discovery_sends_header_and_first_n_rows(tmp_path, cfg):
    src = write_csv(tmp_path / "s.csv", [base_row(i) for i in range(1, 31)])
    good = dict(cfg.column_mapping.mapping)
    llm = FakeLLM(lambda body: _discovery_answer(good))
    p = MigrationPipeline(src, var_dir=tmp_path / "v", llm=llm)
    prop = p.schema_agent.discover()
    prompt = llm.calls[0]["prompt"]
    assert llm.calls[0]["purpose"] == "schema_discovery" and ";".join(HEADER) in prompt
    assert base_row(20)["VIN"] in prompt and base_row(21)["VIN"] not in prompt
    assert not prop.fallback and prop.conflicts == [] and set(prop.column_mapping) == set(HEADER)
    assert p.schema_agent.validate(prop.proposal_id, "ann").mapping["diagnosticFieId"] == \
        "DiagnosticOperation.diagnosticOpID"
    p.close()


def test_discovery_conflicts_block_validation(tmp_path, cfg):
    bad = dict(cfg.column_mapping.mapping)
    bad["toolVersion"] = "Vehicle.VIN"                  # VIN mapped twice, tool version mapped zero times
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", [base_row(1)]), var_dir=tmp_path / "v",
                          llm=FakeLLM(lambda body: _discovery_answer(bad)))
    prop = p.schema_agent.discover()
    assert {c.target_attribute for c in prop.conflicts} == {"Vehicle.VIN", "DiagToolsVersions.DiagToolVersion"}
    with pytest.raises(MappingConflictError):
        p.schema_agent.validate(prop.proposal_id, "ann")
    assert p.schema_agent.validated_mapping() is None
    p.close()


@pytest.mark.parametrize("llm", [FakeLLM(lambda b: "garbage"), FakeLLM(lambda b: LLMFailure("timeout", "t")),
                                 FakeLLM(available=False)])
def test_discovery_reference_fallback(tmp_path, cfg, llm):
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", [base_row(1)]), var_dir=tmp_path / "v", llm=llm)
    prop = p.schema_agent.discover()
    assert prop.fallback and prop.column_mapping == dict(cfg.column_mapping.mapping)
    if not llm.available:
        assert llm.calls == []
    p.close()


def test_pending_mapping_and_header_mismatch_process_zero_rows(tmp_path):
    src = write_csv(tmp_path / "s.csv", [base_row(1), base_row(2)])
    p = MigrationPipeline(src, var_dir=tmp_path / "v")
    info = p.start_campaign()
    rep = p.reports.build(info.run_id)
    assert info.status == "mapping_pending" and rep.analysed == 0 and "pending" in rep.text
    p.seed_reference_mapping()
    header = [h if h != "Vehmodel" else "VehModel" for h in HEADER]
    write_csv(src, [list(base_row(1).values())], header=header)
    info = p.start_campaign()
    rep = p.reports.build(info.run_id)
    assert info.status == "mapping_pending" and rep.analysed == 0
    assert "['VehModel']" in rep.text and "['Vehmodel']" in rep.text
    p.close()


# Answer returned by qwen2.5:7b on the 10,000-row file (4 November 2026): duplicate "toolVersion" key,
# ecuCode and DescriptionDiagECU mapped to the wrong targets, originTool and diagnosticFieId missing.
QWEN_ANSWER = (
    '{"column_mapping": {"VIN": "Vehicle.VIN", "Vehmodel": "Vehicle.VehicleModel", "ecuName": "DiagnozedECU.ecuName", '
    '"ecuCode": "DiagToolsVersions.DiagToolName", "toolVersion": "DiagToolsVersions.DiagToolVersion", '
    '"Creation_date": "DiagnosticOperation.creationDate", "ValueReference": "DiagValueResult.ValueReference", '
    '"DescriptionDiagECU": "DiagValueResult.ValueDescription", "toolVersion": "DiagToolsVersions.DiagToolVersion", '
    '"DiagnosticOperation.diagnosticOpID": "Label"}, "formats": [{"name": "Format_1", "ecuCode_pattern": '
    '"^[A-Z0-9]{12}$"}, {"name": "Format_2", "ecuCode_pattern": "^Id[0-9]{4}$"}], "format_criterion": "x"}')


def test_real_qwen_answer_is_blocked_with_duplicates_and_differences(tmp_path, cfg):
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", [base_row(1)]), var_dir=tmp_path / "v",
                          llm=FakeLLM(lambda body: QWEN_ANSWER))
    prop = p.schema_agent.discover()
    assert not prop.fallback
    dup = [c for c in prop.conflicts if c.kind == "duplicate_column"]
    assert [(c.source_columns, c.target_attribute) for c in dup] == [(["toolVersion"], "DiagToolsVersions.DiagToolVersion")]
    assert "appears 2 times" in dup[0].message
    counts = {c.target_attribute for c in prop.conflicts if c.kind == "target_count"}
    assert counts == {"DiagnosticOperation.diagnosticOpID", "Label"}
    diffs = {d.column: (d.proposed, d.reference) for d in prop.differences_from_reference}
    assert diffs == {
        "originTool": (None, "DiagToolsVersions.DiagToolName"),
        "diagnosticFieId": (None, "DiagnosticOperation.diagnosticOpID"),
        "ecuCode": ("DiagToolsVersions.DiagToolName", "DiagValueResult.ValueDescription"),
        "DescriptionDiagECU": ("DiagValueResult.ValueDescription", "Label"),
    }
    with pytest.raises(MappingConflictError):
        p.schema_agent.validate(prop.proposal_id, "ann")
    assert p.schema_agent.proposal(prop.proposal_id).differences_from_reference == prop.differences_from_reference
    # The operator then asks for the reference mapping and validates it: no difference left.
    ref = p.schema_agent.discover(use_llm=False)
    assert ref.fallback and ref.conflicts == [] and ref.differences_from_reference == []
    assert p.schema_agent.validate(ref.proposal_id, "ann").differences_from_reference == []
    p.close()


def test_same_column_twice_with_the_same_target_is_a_conflict(tmp_path, cfg):
    good = json.dumps({"column_mapping": dict(cfg.column_mapping.mapping)})
    dup = good[:-2] + ', "VIN": "Vehicle.VIN"}}'
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", [base_row(1)]), var_dir=tmp_path / "v",
                          llm=FakeLLM(lambda body: dup))
    prop = p.schema_agent.discover()
    assert [c.kind for c in prop.conflicts] == ["duplicate_column"] and prop.differences_from_reference == []
    p.close()


def test_schema_routes_show_differences(tmp_path):
    from fastapi.testclient import TestClient
    from agents.orchestrator import Services, create_app
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", [base_row(1)]), var_dir=tmp_path / "v",
                          llm=FakeLLM(lambda body: QWEN_ANSWER))
    client = TestClient(create_app(Services(pipeline=p)))
    prop = client.post("/ops/schema/discover").json()
    assert len(prop["differences_from_reference"]) == 4 and prop["conflicts"]
    refused = client.post("/ops/schema/validate", json={"proposal_id": prop["proposal_id"], "operator": "ann"})
    assert refused.status_code == 409 and any(c["kind"] == "duplicate_column" for c in refused.json()["error"]["details"])
    ref = client.post("/ops/schema/discover", params={"source": "reference"}).json()
    assert ref["fallback"] and ref["differences_from_reference"] == []
    ok = client.post("/ops/schema/validate", json={"proposal_id": ref["proposal_id"], "operator": "ann"})
    assert ok.status_code == 200 and ok.json()["differences_from_reference"] == []
    p.close()


# ---------------------------------------------------------------- operator correction (UI mapping step)
def test_operator_correction_resolves_the_conflicts(tmp_path, cfg):
    from agents.api_agent_legacy import MappingValueError
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", [base_row(1)]), var_dir=tmp_path / "v",
                          llm=FakeLLM(lambda body: QWEN_ANSWER))
    agent = p.schema_agent
    prop = agent.discover()
    assert agent.latest_proposal().proposal_id == prop.proposal_id
    partial = dict(prop.column_mapping, originTool="DiagToolsVersions.DiagToolName")
    with pytest.raises(MappingConflictError) as exc:          # diagnosticFieId and Label still unmapped
        agent.validate(prop.proposal_id, "ann", partial)
    still = agent.proposal(exc.value.proposal_id)
    assert {c.target_attribute for c in still.conflicts} >= {"DiagnosticOperation.diagnosticOpID", "Label"}
    assert agent.latest_proposal().proposal_id == still.proposal_id   # the corrected proposal is kept
    fixed = dict(prop.column_mapping, originTool="DiagToolsVersions.DiagToolName",
                 diagnosticFieId="DiagnosticOperation.diagnosticOpID", ecuCode="DiagValueResult.ValueDescription",
                 DescriptionDiagECU="Label")
    validated = agent.validate(prop.proposal_id, "ann", fixed)
    assert validated.mapping == dict(cfg.column_mapping.mapping) and validated.differences_from_reference == []
    assert agent.validated_mapping().validated_by == "ann" and agent.latest_proposal() is None
    with pytest.raises(MappingValueError):
        agent.corrected(prop.proposal_id, dict(fixed, VIN="Vehicle.Colour"))
    with pytest.raises(MappingValueError):
        agent.corrected(prop.proposal_id, {"VIN": "Vehicle.VIN"})
    p.close()


def test_correction_keeping_a_deliberate_difference(tmp_path, cfg):
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", [base_row(1)]), var_dir=tmp_path / "v")
    ref = p.schema_agent.discover(use_llm=False)
    swapped = dict(ref.column_mapping, toolVersion=None)     # leaves DiagToolVersion unmapped: refused
    with pytest.raises(MappingConflictError):
        p.schema_agent.validate(ref.proposal_id, "ann", swapped)
    assert p.schema_agent.validated_mapping() is None
    p.close()


def test_schema_editor_routes(tmp_path, cfg):
    from fastapi.testclient import TestClient
    from agents.orchestrator import Services, create_app
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", [base_row(1)]), var_dir=tmp_path / "v",
                          llm=FakeLLM(lambda body: QWEN_ANSWER))
    client = TestClient(create_app(Services(pipeline=p)))
    ref = client.get("/ops/schema/reference").json()
    assert "Label" in ref["targets"] and ref["mapping"]["VIN"] == "Vehicle.VIN"
    assert client.get("/ops/schema/proposals/latest").status_code == 404
    prop = client.post("/ops/schema/discover").json()
    assert client.get("/ops/schema/proposals/latest").json()["proposal_id"] == prop["proposal_id"]
    bad = client.post("/ops/schema/validate", json={"proposal_id": prop["proposal_id"], "operator": "ann",
                                                    "mapping": dict(prop["column_mapping"], VIN="Nope")})
    assert bad.status_code == 422
    refused = client.post("/ops/schema/validate", json={"proposal_id": prop["proposal_id"], "operator": "ann",
                                                        "mapping": prop["column_mapping"]})
    assert refused.status_code == 409 and "conflict" in refused.json()["error"]["message"]
    ok = client.post("/ops/schema/validate", json={"proposal_id": prop["proposal_id"], "operator": "ann",
                                                   "mapping": ref["mapping"]})
    assert ok.status_code == 200 and ok.json()["validated_by"] == "ann"
    assert client.get("/ops/schema/mapping").json()["differences_from_reference"] == []
    p.close()
