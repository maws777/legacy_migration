import json
from datetime import datetime, timezone

import httpx
import pytest

from ui.api_client import ApiCallError, OrchestratorClient

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc).isoformat()
RUN = {"run_id": "r1", "campaign_id": "c1", "kind": "full", "status": "completed", "campaign_status": "paused",
       "started_at": NOW, "ended_at": NOW, "open_questions": 2}
Q = {"question_id": "q1", "run_id": "r1", "pattern_key": "ecuName|UNREADABLE", "field": "ecuName",
     "reason": "UNREADABLE", "rule_id": "R5", "row_count": 2, "example_rows": [], "missing": "m", "why": "w",
     "llm_suggestion": None, "llm_status": "not_applicable", "allowed_values": ["BCM"],
     "options": ["reject_group", "case_by_case"], "status": "open", "parent_question_id": None,
     "created_at": NOW, "closed_at": None}


def make(routes):
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, dict(req.url.params), req.content))
        status, body = routes[(req.method, req.url.path)]
        return httpx.Response(status, json=body)

    return OrchestratorClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://o")), seen


def test_calls_are_typed():
    api, seen = make({
        ("GET", "/ops/runs"): (200, [RUN]),
        ("POST", "/ops/campaigns"): (202, RUN),
        ("POST", "/ops/campaigns/c1/resume"): (202, RUN),
        ("GET", "/ops/questions"): (200, [Q]),
        ("POST", "/ops/questions/q1/answer"): (200, {"question": {**Q, "status": "closed"}, "decision": None,
                                                     "created_questions": ["a", "b"]}),
        ("GET", "/ops/runs/r1/decisions"): (200, {"total": 0, "items": []}),
        ("GET", "/ops/phase"): (200, {"current_phase": 2, "pending_target": None, "gate": [], "served": {}}),
        ("POST", "/ops/phase/transition"): (200, {"status": "refused", "current_phase": 2, "previous_phase": None,
                                                  "reason": "cannot be skipped"}),
    })
    assert api.runs()[0].run_id == "r1"
    assert api.start_campaign("op").campaign_status == "paused"
    assert api.resume("c1", "op").run_id == "r1"
    assert api.questions()[0].pattern_key == "ecuName|UNREADABLE"
    assert api.answer("q1", "case_by_case", "op").created_questions == ["a", "b"]
    assert api.decisions("r1", action="REJECT", field="").total == 0
    assert seen[-1][2] == {"action": "REJECT", "limit": "200", "offset": "0"}
    assert api.phase().current_phase == 2
    assert api.transition(4, "op").reason == "cannot be skipped"
    assert json.loads(seen[-1][3]) == {"target_phase": 4, "operator": "op"}


def test_human_decisions_are_listed_and_revoked():
    d = {"pattern_key": "ecuCode|X", "kind": "value", "value": "ID1", "answered_at": NOW, "operator": "bob",
         "question_id": "q1", "field": "ecuCode", "row_count": 3}
    api, seen = make({("GET", "/ops/decisions"): (200, [d]), ("POST", "/ops/decisions/revoke"): (200, [])})
    assert api.decisions_made()[0].row_count == 3
    assert api.revoke_decision("ecuCode|X", "ann") == []
    assert json.loads(seen[-1][3]) == {"pattern_key": "ecuCode|X", "operator": "ann"}


@pytest.mark.parametrize("status,body,fragment", [
    (409, {"error": {"code": "CONFLICT", "message": "question q1 is closed", "details": None}}, "closed"),
    (422, {"error": {"code": "VALIDATION_ERROR", "message": "R5 ecuName in ECUDico", "details": {}}}, "ECUDico"),
    (422, {"detail": [{"loc": ["body", "operator"], "msg": "too short"}]}, "operator"),
])
def test_errors_name_the_operation(status, body, fragment):
    api, _ = make({("POST", "/ops/questions/q1/answer"): (status, body)})
    with pytest.raises(ApiCallError) as exc:
        api.answer("q1", "apply_value", "op", "X")
    assert exc.value.operation == "answer question" and exc.value.status == status and fragment in str(exc.value)


def test_unreachable_api():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    api = OrchestratorClient(client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://o"))
    with pytest.raises(ApiCallError) as exc:
        api.phase()
    assert exc.value.operation == "load phase" and "unreachable" in exc.value.message


def test_latest_reconciliation_none_on_404():
    api, _ = make({("GET", "/ops/reconciliation/latest"): (404, {"error": {"code": "NOT_FOUND", "message": "none"}})})
    assert api.latest_reconciliation() is None


def test_schema_mapping_is_none_before_validation_and_validate_posts_the_proposal():
    api, seen = make({("GET", "/ops/schema/mapping"): (404, {"error": {"code": "NOT_FOUND", "message": "none"}})})
    assert api.schema_mapping() is None
    api, seen = make({("POST", "/ops/schema/validate"): (200, {"mapping": {"VIN": "vin"}, "validated_by": "ann",
                                                               "validated_at": NOW})})
    assert api.validate_schema("p1", "ann").validated_by == "ann"
    assert json.loads(seen[0][3]) == {"proposal_id": "p1", "operator": "ann"}
