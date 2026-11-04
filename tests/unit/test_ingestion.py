import hashlib

import pytest

from legacy_a.csv_reader import SourceReadError, read_source
from migration.ingestion import IngestionError, ingest
from tests.fixtures.make_csv import HEADER, base_row, write_csv


@pytest.mark.parametrize("newline", ["\r\n", "\n"])
@pytest.mark.parametrize("trailing", [True, False])
def test_bom_line_endings_and_trailing_newline(tmp_path, newline, trailing):
    p = write_csv(tmp_path / "s.csv", [base_row(1), base_row(2)], newline=newline, trailing_newline=trailing)
    src = read_source(p)
    assert src.header == HEADER                       # BOM removed from the first column
    assert [r.source_row_id for r in src.rows] == [1, 2]
    assert all(not r.malformed for r in src.rows)
    assert src.rows[1].values["VIN"] == base_row(2)["VIN"]


def test_cells_are_exact_strings(tmp_path):
    raw = base_row(1, toolVersion="007", ValueReference="2,04E+12", ecuName=" OBC ", Vehmodel="#NOM?",
                   DescriptionDiagECU="Lecture référence logiciel ADAS", Creation_date="")
    src = read_source(write_csv(tmp_path / "s.csv", [raw]))
    assert dict(src.rows[0].values) == raw


def test_short_and_long_rows_are_malformed(tmp_path):
    cells = list(base_row(1).values())
    p = write_csv(tmp_path / "s.csv", [cells[:-1], cells + ["extra"], cells])
    src = read_source(p)
    assert [r.malformed for r in src.rows] == [True, True, False]
    assert [r.source_row_id for r in src.rows] == [1, 2, 3]


def test_invalid_utf8_and_missing_file(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_bytes(b"\xef\xbb\xbfVIN;x\r\n\xff\xfe;1\r\n")
    with pytest.raises(SourceReadError) as exc:
        read_source(p)
    assert "UTF-8" in exc.value.reason
    with pytest.raises(SourceReadError) as exc:
        read_source(tmp_path / "absent.csv")
    assert "cannot open" in exc.value.reason


def test_file_bytes_unchanged_and_sha256(tmp_path, cfg):
    p = write_csv(tmp_path / "s.csv", [base_row(1)])
    before = p.read_bytes()
    res = ingest(p, cfg.column_mapping.mapping, cfg.column_mapping)
    assert p.read_bytes() == before
    assert res.sha256 == hashlib.sha256(before).hexdigest()


def test_missing_required_columns_all_reported(tmp_path, cfg):
    header = [h for h in HEADER if h not in ("diagnosticFieId", "Vehmodel")] + ["diagnosticFieldId"]
    p = write_csv(tmp_path / "s.csv", [["x"] * len(header)], header=header)
    with pytest.raises(IngestionError) as exc:
        ingest(p, cfg.column_mapping.mapping, cfg.column_mapping)
    assert set(exc.value.missing_columns) == {"diagnosticFieId", "Vehmodel"}


def test_column_names_are_case_sensitive(tmp_path, cfg):
    header = ["vin" if h == "VIN" else h for h in HEADER]
    p = write_csv(tmp_path / "s.csv", [["x"] * len(header)], header=header)
    with pytest.raises(IngestionError) as exc:
        ingest(p, cfg.column_mapping.mapping, cfg.column_mapping)
    assert exc.value.missing_columns == ("VIN",)


def test_renaming_through_a_validated_mapping(tmp_path, cfg):
    header = ["CHASSIS" if h == "VIN" else h for h in HEADER]
    mapping = {("CHASSIS" if c == "VIN" else c): t for c, t in cfg.column_mapping.mapping.items()}
    p = write_csv(tmp_path / "s.csv", [list(base_row(1).values())], header=header)
    res = ingest(p, mapping, cfg.column_mapping)
    assert res.rows[0].values["VIN"] == base_row(1)["VIN"] and "CHASSIS" not in res.rows[0].values


def test_reference_table_round_trip(tmp_path):
    """Requirement 1.6: a reference table written as UTF-8 BOM CRLF is ingested identically."""
    header = ["colA", "colB", "colC"]
    table = [["", "007", "#NOM?"], ["éàü", "Mégane", ""], ["0001", "a b", "Ünïcödé"], ["", "", ""]]
    p = write_csv(tmp_path / "ref.csv", table, header=header, bom=True, newline="\r\n")
    src = read_source(p)
    assert list(src.header) == header
    assert [[r.values[h] for h in header] for r in src.rows] == table
