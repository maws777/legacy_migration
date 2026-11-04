"""Legacy_B_Simulator: Legacy B data generated from the Legacy A VINs, with planted model conflicts.

Run: ``python -m legacy_b.simulator [--source CSV] [--out DIR]``.
"""
from __future__ import annotations

import argparse
import random
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from legacy_a.csv_reader import SourceFile, read_source
from rules.config import RuleConfig, load_rule_config
from rules.fields import dict_lookup, normalize_vin

DEFAULT_OUT = Path(__file__).resolve().parent / "data"
RECORDS_FILE = "legacy_b_records.csv"
CONFLICTS_FILE = "legacy_b_conflicts.csv"


@dataclass(frozen=True)
class PlantedConflict:
    vin: str
    legacy_a_model: str
    legacy_b_modele: str


def to_mixed_case(canonical: str) -> str:
    """Split on spaces and dashes keeping the separators, capitalize each part."""
    return "".join(p.capitalize() if p not in (" ", "-") else p for p in re.split(r"([ -])", canonical))


def _write(path: Path, header: str, lines: list[str]) -> None:
    path.write_bytes(("\n".join([header] + lines) + "\n").encode("utf-8"))


def generate(source: SourceFile, cfg: RuleConfig, out_dir: Path | str = DEFAULT_OUT,
             vin_column: str = "VIN", model_column: str = "Vehmodel") -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    models_dico = list(cfg.vehicle_models)
    per_vin: dict[str, set[str]] = defaultdict(set)
    for r in source.rows:
        if r.malformed:
            continue
        v = normalize_vin(r.values.get(vin_column, ""))
        m = dict_lookup(r.values.get(model_column, ""), models_dico)
        per_vin[v]
        if m is not None:
            per_vin[v].add(m)
    vins = sorted(per_vin)
    rng = random.Random(cfg.pipeline.legacy_b.seed)
    eligible = [v for v in vins if len(per_vin[v]) == 1]
    planted = set(rng.sample(eligible, min(cfg.pipeline.legacy_b.conflicts, len(eligible))))
    records, conflicts = [], []
    for v in vins:
        if v in planted:
            (model,) = per_vin[v]
            other = rng.choice(sorted(m for m in models_dico if m != model))
            modele = to_mixed_case(other)
            conflicts.append(PlantedConflict(v, model, modele))
        elif v in per_vin and len(per_vin[v]) == 1:
            modele = to_mixed_case(next(iter(per_vin[v])))
        else:
            modele = to_mixed_case(rng.choice(models_dico))
        date_maj = (date(2020, 1, 1) + timedelta(days=rng.randrange(2192))).strftime("%Y%m%d")
        records.append(f"{v};{modele};{date_maj}")
    rec_path, conf_path = out_dir / RECORDS_FILE, out_dir / CONFLICTS_FILE
    _write(rec_path, "CHASSIS_NO;MODELE;DATE_MAJ", records)
    _write(conf_path, "VIN;LEGACY_A_MODEL;LEGACY_B_MODELE",
           [f"{c.vin};{c.legacy_a_model};{c.legacy_b_modele}" for c in conflicts])
    return rec_path, conf_path


def read_conflicts(path: Path | str) -> list[PlantedConflict]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()[1:]
    return [PlantedConflict(*line.split(";")) for line in lines if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m legacy_b.simulator")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    cfg = load_rule_config()
    src = read_source(args.source or cfg.pipeline.paths.source_csv)
    rec, conf = generate(src, cfg, args.out)
    print(f"written {rec} and {conf} ({len(read_conflicts(conf))} planted conflicts)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
