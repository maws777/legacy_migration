"""Target_DB schema (design "Target_DB"): portable types, named constraints, regexp_match() CHECKs."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Engine, ForeignKeyConstraint, Integer, MetaData,
    PrimaryKeyConstraint, String, Table, and_, create_engine, event, func, or_,
)
from sqlalchemy.pool import StaticPool

from rules.config import RuleConfig

VIN_PATTERN = "^[0-9A-Z]{20}$"
VALUE_REF_PATTERN = "^[0-9A-Z]{10}$"
TABLE_ORDER = (
    "vehicle", "diagnozed_ecu", "vehicle_ecu", "diag_tools_versions",
    "diagnostic_operation", "diag_value_result",
)


def build_metadata(cfg: RuleConfig) -> MetaData:
    """Six target tables. Dictionary CHECKs are built from Rule_Config."""
    md = MetaData()
    vehicle = Table(
        "vehicle", md,
        Column("vin", String(20), nullable=False),
        Column("vehicle_model", String(32), nullable=False),
        PrimaryKeyConstraint("vin", name="pk_vehicle"),
    )
    vehicle.append_constraint(CheckConstraint(
        and_(func.length(vehicle.c.vin) == 20, vehicle.c.vin.regexp_match(VIN_PATTERN)),
        name="ck_vehicle_vin_format"))
    vehicle.append_constraint(CheckConstraint(
        vehicle.c.vehicle_model.in_(list(cfg.vehicle_models)), name="ck_vehicle_model_dico"))

    ecu = Table(
        "diagnozed_ecu", md,
        Column("ecu_name", String(16), nullable=False),
        PrimaryKeyConstraint("ecu_name", name="pk_diagnozed_ecu"),
    )
    ecu.append_constraint(CheckConstraint(ecu.c.ecu_name.in_(list(cfg.ecus)), name="ck_ecu_name_dico"))

    Table(
        "vehicle_ecu", md,
        Column("vin", String(20), nullable=False),
        Column("ecu_name", String(16), nullable=False),
        PrimaryKeyConstraint("vin", "ecu_name", name="pk_vehicle_ecu"),
        ForeignKeyConstraint(["vin"], ["vehicle.vin"], name="fk_vehicle_ecu_vehicle"),
        ForeignKeyConstraint(["ecu_name"], ["diagnozed_ecu.ecu_name"], name="fk_vehicle_ecu_ecu"),
    )

    tools = Table(
        "diag_tools_versions", md,
        Column("diag_tool_name", String(16), nullable=False),
        Column("diag_tool_version", String(64), nullable=False),
        PrimaryKeyConstraint("diag_tool_name", "diag_tool_version", name="pk_diag_tools_versions"),
    )
    tools.append_constraint(CheckConstraint(
        tools.c.diag_tool_name.in_(list(cfg.tools)), name="ck_tool_name_dico"))

    Table(
        "diagnostic_operation", md,
        Column("diagnostic_op_id", String(128), nullable=False),
        Column("creation_date", DateTime, nullable=False),
        Column("vin", String(20), nullable=False),
        Column("ecu_name", String(16), nullable=False),
        Column("diag_tool_name", String(16), nullable=False),
        Column("diag_tool_version", String(64), nullable=False),
        Column("source_origin_tool", String(128), nullable=False),
        Column("ingestion_ts", DateTime, nullable=False),
        Column("run_id", String(36), nullable=False),
        Column("source_row_id", Integer, nullable=False),
        Column("op_id_generated", Boolean(create_constraint=False), nullable=False),
        PrimaryKeyConstraint("diagnostic_op_id", name="pk_diagnostic_operation"),
        ForeignKeyConstraint(["vin", "ecu_name"], ["vehicle_ecu.vin", "vehicle_ecu.ecu_name"],
                             name="fk_operation_vehicle_ecu"),
        ForeignKeyConstraint(["diag_tool_name", "diag_tool_version"],
                             ["diag_tools_versions.diag_tool_name", "diag_tools_versions.diag_tool_version"],
                             name="fk_operation_tool_version"),
    )

    result = Table(
        "diag_value_result", md,
        Column("diagnostic_op_id", String(128), nullable=False),
        Column("value_reference", String(32), nullable=False),
        Column("value_description", String(6), nullable=False),
        PrimaryKeyConstraint("diagnostic_op_id", name="pk_diag_value_result"),
        ForeignKeyConstraint(["diagnostic_op_id"], ["diagnostic_operation.diagnostic_op_id"],
                             name="fk_value_result_operation"),
    )
    result.append_constraint(CheckConstraint(
        result.c.value_description.in_(list(cfg.value_codes)), name="ck_value_description_dico"))
    result.append_constraint(CheckConstraint(
        or_(result.c.value_description == "ID0001",
            and_(func.length(result.c.value_reference) == 10,
                 result.c.value_reference.regexp_match(VALUE_REF_PATTERN))),
        name="ck_value_reference_format"))
    return md


def _sqlite_file(url: str) -> Path | None:
    prefix = "sqlite:///"
    if url.startswith(prefix) and ":memory:" not in url and "mode=memory" not in url:
        return Path(url[len(prefix):].split("?", 1)[0])
    return None


def make_engine(url: str, read_only: bool = False) -> Engine:
    """SQLite engine with ``PRAGMA foreign_keys=ON`` (and WAL for files) on every connection.

    ``read_only`` adds ``PRAGMA query_only=ON`` (Target_API, Query_Agent).
    """
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    path = _sqlite_file(url)
    if path is None:
        kwargs["poolclass"] = StaticPool
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record) -> None:  # pragma: no cover - exercised by every test
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        if path is not None:
            if not read_only:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=10000")
        if read_only:
            cur.execute("PRAGMA query_only=ON")
        cur.close()

    return engine


def sqlite_url(path: str | Path) -> str:
    return f"sqlite:///{Path(path).as_posix()}"


def create_target_schema(engine: Engine, cfg: RuleConfig) -> MetaData:
    md = build_metadata(cfg)
    md.create_all(engine)
    return md
