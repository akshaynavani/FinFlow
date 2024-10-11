"""Command-line interface: ``finflow <command>``."""

from __future__ import annotations

import argparse
import json
import sys

from finflow import __version__
from finflow.config import load_config
from finflow.logging_utils import configure_logging, new_run_id, run_context
from finflow.pipeline import Pipeline
from finflow.quality import DataQualityError
from finflow.sample_data import generate_dataset


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finflow", description=__doc__)
    parser.add_argument("--version", action="version", version=f"finflow {__version__}")
    parser.add_argument("-c", "--config", help="Path to pipeline YAML config")
    parser.add_argument("--log-level", help="Override log level (DEBUG, INFO, ...)")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Generate a synthetic sample dataset")
    gen.add_argument("--out", help="Output directory (defaults to config data_dir)")
    gen.add_argument("--days", type=int, default=504)
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--start", default="2022-01-03")
    gen.add_argument("--with-defects", action="store_true", help="Inject dirty rows")

    run = sub.add_parser("run", help="Run the full ELT pipeline")
    run.add_argument("--force", action="store_true", help="Reload unchanged sources")
    run.add_argument("--skip-checks", action="store_true")

    sub.add_parser("extract", help="Extract and load raw sources only").add_argument(
        "--force", action="store_true"
    )
    tr = sub.add_parser("transform", help="Run SQL models")
    tr.add_argument("--select", nargs="*", help="Only run models matching these tokens")

    chk = sub.add_parser("check", help="Run a data quality suite")
    chk.add_argument("suite", choices=["raw", "staging", "features"])
    chk.add_argument("--no-fail", action="store_true", help="Exit 0 even on errors")

    sub.add_parser("features", help="Build feature table from marts")
    sub.add_parser("tables", help="List warehouse tables with row counts")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.log_level:
        config.log_level = args.log_level
    configure_logging(config.log_level, config.log_file)

    if args.command == "generate":
        paths = generate_dataset(
            args.out or config.data_dir,
            start=args.start,
            days=args.days,
            seed=args.seed,
            with_defects=args.with_defects,
        )
        print(json.dumps({k: str(v) for k, v in paths.items()}, indent=2))
        return 0

    pipeline = Pipeline(config)
    try:
        if args.command == "run":
            summary = pipeline.run(force=args.force, skip_checks=args.skip_checks)
            print(json.dumps({"run_id": summary.run_id, "status": summary.status,
                              "quality": summary.quality}, indent=2))
        elif args.command == "extract":
            run_id = new_run_id()
            with run_context(run_id=run_id):
                print(json.dumps(pipeline.extract_load(run_id, force=args.force), indent=2))
        elif args.command == "transform":
            print(json.dumps(pipeline.transform(select=args.select), indent=2))
        elif args.command == "check":
            if args.no_fail:
                config.fail_on_error = False
            report = pipeline.check(args.suite)
            print(json.dumps(report.summary(), indent=2))
            for r in report.errors + report.warnings:
                print(f"  [{r.severity.upper()}] {r.table}.{r.name}: {r.failing_rows} ({r.detail})")
        elif args.command == "features":
            print(json.dumps(pipeline.features(), indent=2))
        elif args.command == "tables":
            for table in pipeline.wh.tables():
                print(f"{table:32s} {pipeline.wh.row_count(table):>10,d}")
    except DataQualityError as exc:
        print(f"Data quality failure: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"Missing file: {exc}", file=sys.stderr)
        return 1
    finally:
        pipeline.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
