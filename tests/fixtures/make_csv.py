"""Synthetic Legacy A CSV builder with the anomalies of migration-rules.md.

No test except the ``dataset`` ones depends on the real file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping, Sequence

HEADER: tuple[str, ...] = (
    "originTool", "toolVersion", "VIN", "diagnosticFieId", "ecuName", "ecuCode",
    "DescriptionDiagECU", "ValueReference", "Creation_date", "Vehmodel",
)


def vin(n: int) -> str:
    """Deterministic valid 20-character VIN."""
    return f"VF1AB{n:015d}"


def base_row(n: int = 1, **overrides: str) -> dict[str, str]:
    """A clean Format_1 row (MIGRATE)."""
    row = {
        "originTool": "DDT2000",
        "toolVersion": "v1.2",
        "VIN": vin(n),
        "diagnosticFieId": f"OP{n:06d}",
        "ecuName": "BCM",
        "ecuCode": "ID0002",
        "DescriptionDiagECU": "Lecture référence pièce BCM",
        "ValueReference": f"AB{n:08d}",
        "Creation_date": "15/03/2024 10:30",
        "Vehmodel": "CLIO",
    }
    row.update(overrides)
    return row


def format2_row(n: int, did: str | None = "F188", ecu: str = "AIRBAG", code: str = "Id123",
                **overrides: str) -> dict[str, str]:
    label = f"Donnée diagnostic {ecu} ({did})" if did else f"Donnée diagnostic {ecu}"
    row = base_row(n, ecuCode=code, ecuName=ecu, DescriptionDiagECU=label)
    row.update(overrides)
    return row


def anomaly_rows() -> list[dict[str, str]]:
    """One row per anomaly family of migration-rules.md, VINs 1..N."""
    rows = [
        base_row(1),                                            # clean MIGRATE
        base_row(2, VIN="vf1-ab00 0000000000002"),              # R1 CORRECT
        base_row(3, originTool="DiagTool"),                     # R2 TRANSFORM
        base_row(4, originTool="AfterSalesDiag"),
        base_row(5, ecuName="T CM"),                            # R5 CORRECT
        base_row(6, ecuName="E-PS"),
        base_row(7, ecuName="HVAC-"),
        base_row(8, ecuName=" OBC"),
        base_row(9, Vehmodel="AR KANA"),                        # R6 CORRECT
        base_row(10, Vehmodel="TRAFIC-"),
        base_row(11, Vehmodel="#NOM?"),                         # R6 unreadable
        base_row(12, ValueReference="2,04E+12"),                # R4 REJECT
        base_row(13, ValueReference="#NOM?"),
        base_row(14, ValueReference=""),
        base_row(15, ValueReference="AB12/45678"),
        base_row(16, ValueReference="AB123"),
        base_row(17, originTool=""),                            # R8 ASK_HUMAN
        base_row(18, toolVersion=""),
        base_row(19, Creation_date=""),
        base_row(20, diagnosticFieId=""),                       # R8 generated id
        base_row(21, ecuName="#NOM?"),                          # R5 label ECU
        format2_row(22, did="F188"),                            # R3 DID mapped
        format2_row(23, did="F738", ecu="HVAC"),                # R3 out of scope
        format2_row(24, did=None),                              # R3 no DID
        base_row(25, diagnosticFieId="SHARED01"),               # R7 shared id
        base_row(26, diagnosticFieId="SHARED01", ValueReference="CD00000026"),
    ]
    return rows


def write_csv(path: Path, rows: Iterable[Mapping[str, str] | Sequence[str]],
              header: Sequence[str] = HEADER, *, bom: bool = True, newline: str = "\r\n",
              trailing_newline: bool = True) -> Path:
    """Write a ``;``-separated UTF-8 file. Rows are dicts (by header name) or raw cell lists.

    Cells must not contain ``;``, double quotes, CR or LF.
    """
    lines = [";".join(header)]
    for row in rows:
        cells = [row.get(h, "") for h in header] if isinstance(row, Mapping) else list(row)
        lines.append(";".join(cells))
    text = newline.join(lines) + (newline if trailing_newline else "")
    data = text.encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)
    return path


def edge_case_rows() -> list[dict[str, str]]:
    """Unknown_Variants never seen in the 10,000-row file (decisions.md), one VIN per row."""
    plain = "Lecture référence pièce"                 # label without ECU name: no Label_ECU deduction
    return [
        base_row(101, Vehmodel="Mégane E-Tech"),
        base_row(102, originTool="CLIP"),
        base_row(103, originTool="Diag Tool"),
        base_row(104, ecuName="AIR-BAG UNIT", DescriptionDiagECU=plain),
        base_row(105, Creation_date="2024-03-15 10:30"),
        base_row(106, originTool="CLIP"),
        base_row(107, ecuCode="ID 0004"),
        base_row(108),
    ]
