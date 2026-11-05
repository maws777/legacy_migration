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


def warmup(config_dir: Path) -> int:
    """Load the model and wait for one short answer: prints "Model ready" or the error, exit code 0 or 1."""
    from agents.llm_client import LLMClient
    from target.schema import make_engine
    try:
        llm_cfg = load_rule_config(config_dir).pipeline.llm
    except ConfigError as exc:
        print(f"LLM warm-up failed: Rule_Config {exc.file}: {exc.error}", file=sys.stderr)
        return 1
    print(f"Loading {llm_cfg.provider} model {llm_cfg.model} (up to {llm_cfg.timeout_s:g} s)...", flush=True)
    ok, seconds, detail = LLMClient(llm_cfg, make_engine("sqlite:///:memory:")).warm_up()
    if ok:
        print(f"Model ready in {seconds:g} s: {detail}")
        return 0
    print(f"Model NOT ready after {seconds:g} s: {detail}. Runs will use Degraded_Mode.", file=sys.stderr)
    return 1


def print_differences(differences) -> None:
    if not differences:
        print("differences from the reference Column_Mapping: none")
        return
    print(f"differences from the reference Column_Mapping ({len(differences)} column(s)):")
    for d in differences:
        print(f"  {d.column}: proposed {d.proposed} | reference {d.reference}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m migration")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="start a campaign (first Run) on the source CSV")
    run.add_argument("--source", type=Path)
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG_DIR)
    run.add_argument("--var", type=Path)
    run.add_argument("--operator", default=None)
    run.add_argument("--no-llm", action="store_true",
                     help="Degraded_Mode on purpose: no LLM call, Unknown_Variants go to humans")
    schema = sub.add_parser("schema", help="discover the Column_Mapping and validate it as OPERATOR")
    schema.add_argument("--operator", required=True)
    schema.add_argument("--no-llm", action="store_true", help="propose the reference Column_Mapping, no LLM call")
    warm = sub.add_parser("warmup", help="load the configured LLM model and send one short request")
    warm.add_argument("--config", type=Path, default=DEFAULT_CONFIG_DIR)
    for p in (schema,):
        p.add_argument("--source", type=Path)
        p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_DIR)
        p.add_argument("--var", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "warmup":
        return warmup(args.config)

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
    if getattr(args, "no_llm", False):
        from agents.llm_client import NoLLM
        components["llm"] = NoLLM()
    pipeline = MigrationPipeline(source, var_dir=var_dir, config_dir=args.config, **components)
    if args.cmd == "schema":
        try:
            pipeline.ensure_llm()
            proposal = pipeline.schema_agent.discover(use_llm=not args.no_llm)
            origin = "reference Column_Mapping" if proposal.fallback else "LLM proposal"
            print(f"proposal {proposal.proposal_id} ({origin})")
            for column, target in proposal.column_mapping.items():
                print(f"  {column} -> {target}")
            print_differences(proposal.differences_from_reference)
            if proposal.conflicts:
                print(f"{len(proposal.conflicts)} conflict(s), validation refused:", file=sys.stderr)
                for c in proposal.conflicts:
                    print(f"  - {c.message or c.target_attribute}", file=sys.stderr)
                print("Run again with --no-llm to validate the reference Column_Mapping.", file=sys.stderr)
                return 1
            validated = pipeline.schema_agent.validate(proposal.proposal_id, args.operator)
            print(f"Column_Mapping validated by {args.operator} "
                  f"({len(validated.differences_from_reference)} difference(s) from the reference accepted)")
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
