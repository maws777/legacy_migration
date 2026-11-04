"""Command line: ``python -m migration run``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rules.config import DEFAULT_CONFIG_DIR, ConfigError, PathsConfig, load_rule_config


def _paths(config_dir: Path) -> PathsConfig:
    try:
        return load_rule_config(config_dir).pipeline.paths
    except ConfigError:
        return PathsConfig()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m migration")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="start a campaign (first Run) on the source CSV")
    run.add_argument("--source", type=Path)
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG_DIR)
    run.add_argument("--var", type=Path)
    run.add_argument("--operator", default=None)
    schema = sub.add_parser("schema", help="discover the Column_Mapping and validate it as OPERATOR")
    schema.add_argument("--operator", required=True)
    for p in (schema,):
        p.add_argument("--source", type=Path)
        p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_DIR)
        p.add_argument("--var", type=Path)
    args = parser.parse_args(argv)

    from migration.pipeline import MigrationPipeline

    paths = _paths(args.config)
    source = args.source or Path(paths.source_csv)
    var_dir = args.var or Path(paths.ops_db).parent
    components = {}
    try:
        cfg = load_rule_config(args.config)
        if cfg.pipeline.legacy_b_lookup:
            from agents.api_agent_legacy_b import ApiAgentLegacyB
            components["legacy_b"] = ApiAgentLegacyB.from_url(cfg.pipeline.services.legacy_b, cfg)
    except ConfigError:
        pass                                   # the Run reports the configuration error
    pipeline = MigrationPipeline(source, var_dir=var_dir, config_dir=args.config, **components)
    if args.cmd == "schema":
        try:
            pipeline.ensure_llm()
            proposal = pipeline.schema_agent.discover()
            print(f"proposal {proposal.proposal_id} (reference fallback: {proposal.fallback})")
            for column, target in proposal.column_mapping.items():
                print(f"  {column} -> {target}")
            if proposal.conflicts:
                print(f"conflicts: {[c.model_dump() for c in proposal.conflicts]}", file=sys.stderr)
                return 1
            pipeline.schema_agent.validate(proposal.proposal_id, args.operator)
            print(f"Column_Mapping validated by {args.operator}")
            return 0
        finally:
            pipeline.close()
    try:
        info = pipeline.start_campaign(args.operator)
        report = pipeline.report_text(info.run_id)
    finally:
        pipeline.close()
    print(f"run_id: {info.run_id}  campaign_id: {info.campaign_id}  status: {info.status}"
          f"  campaign: {info.campaign_status}")
    if info.error:
        print(f"error: {info.error}", file=sys.stderr)
    if report:
        print(report)
    return 1 if info.status == "failed" else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
