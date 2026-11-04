from datetime import datetime, timezone

import pytest

from migration.context import build_shared_op_ids, build_vin_model_index
from reporting.decision_log import DecisionLogError, DecisionLogger, UnknownRunError
from reporting.schemas import DecisionRecord, LogFilters
from rules.types import OperationIdentity, RowScan
from target.schema import make_engine


@pytest.fixture
def logger():
    return DecisionLogger(make_engine("sqlite:///:memory:"))


def rec(run="r1", row=1, field="VIN", action="MIGRATE", conf=None, by="rule", expl="ok", **kw):
    base = dict(run_id=run, source_row=row, field=field, action=action, rule_id="R1", reason="OK",
                before="a", after="a", decided_by=by, confidence=conf, explanation=expl, tokens_used=0,
                timestamp=datetime(2026, 11, 4, 10, 0, 0, 123456, tzinfo=timezone.utc))
    base.update(kw)
    return DecisionRecord(**base)


def test_query_filters_and_earlier_runs_unchanged(logger):
    logger.write([rec(row=1), rec(row=1, field="_row"), rec(row=2, field="_row", action="REJECT")])
    logger.write([rec(run="r2", row=1, field="_row")])
    assert logger.query("r1").total == 3
    assert logger.query("r1", LogFilters(action="REJECT")).items[0].source_row == 2
    assert logger.query("r1", LogFilters(source_row=1, field="VIN")).total == 1
    assert logger.row_counts("r1") == {"MIGRATE": 1, "REJECT": 1}
    assert logger.query("r2").total == 1 and logger.query("r1").total == 3


def test_second_row_record_is_refused_and_names_the_row(logger):
    logger.write([rec(row=5, field="_row")])
    with pytest.raises(DecisionLogError) as exc:
        logger.write([rec(row=4, field="ecuName"), rec(row=5, field="_row")])
    assert exc.value.run_id == "r1" and exc.value.source_row == 5
    assert logger.query("r1").total == 1                       # nothing of the failed batch written


@pytest.mark.parametrize("fmt", ["json", "csv"])
def test_export_import_round_trip(logger, tmp_path, fmt):
    originals = [
        rec(row=1, conf=None, expl="Décision: « Mégane » → MEGANE E-TECH, ü, 中文"),
        rec(row=2, field="ecuName", action="CORRECT", by="llm", conf=0.93, expl='quote " and, comma',
            tokens_used=140, before=" T CM", after="TCM"),
        rec(row=3, field="ValueReference", before="", after="", expl="multi\nline"),
    ]
    logger.write(originals)
    path = logger.export("r1", fmt, tmp_path)
    assert path.name == f"decision_log_r1.{fmt}"
    assert DecisionLogger.import_file(path) == originals
    if fmt == "csv":
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_export_unknown_run(logger, tmp_path):
    with pytest.raises(UnknownRunError):
        logger.export("nope", "json", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_record_requires_explanation():
    with pytest.raises(ValueError):
        rec(expl="")


def _scan(vin, model, op, ident):
    return RowScan(vin, model, op, OperationIdentity(vin, ident, None, None, None), ())


def test_context_builders():
    scans = [_scan("V1", "CLIO", "A", "BCM"), _scan("V1", "MASTER", "A", "BCM"), _scan("V2", None, "B", "BCM"),
             _scan("V2", "CLIO", "B", "ABS"), _scan("V3", "CLIO", None, "BCM")]
    assert build_vin_model_index(scans) == {"V1": frozenset({"CLIO", "MASTER"}), "V2": frozenset({"CLIO"}),
                                            "V3": frozenset({"CLIO"})}
    # "A" is carried by two rows with the same identity tuple -> not shared
    assert build_shared_op_ids(scans) == frozenset({"B"})
