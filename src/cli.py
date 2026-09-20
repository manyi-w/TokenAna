"""Configuration inspection and read-only experiment planning."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from .components import ConfigError, read_toml
from .config import config_dict, load_experiment
from .accounting import render_accounting


def main() -> int:
    parser = argparse.ArgumentParser(prog="tokenAna")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("config", help="Inspect configuration without running components")
    inspect.add_argument("path", help="Path to the experiment TOML file")
    run = commands.add_parser("run", help="Plan or execute a new experiment")
    run.add_argument("path", help="Path to the experiment TOML file")
    run.add_argument("--dry-run", action="store_true", help="Only display a read-only plan")
    run.add_argument("--runtime", type=Path, help="Prepared Linux runtime TOML")
    run.add_argument("--output", type=Path, help="New output directory; never overwrite an existing run")
    resume = commands.add_parser("resume", help="Resume an existing experiment at a task boundary")
    resume.add_argument("path", help="Path to the experiment TOML file")
    resume.add_argument("--runtime", type=Path, required=True, help="Same runtime TOML used by the run")
    resume.add_argument("--output", type=Path, required=True, help="Existing output directory")
    analyze = commands.add_parser("analyze", help="Recompute accounting offline from saved run artifacts")
    analyze.add_argument("path", type=Path, help="Existing run directory")
    analyze.add_argument("--output", type=Path, help="New analysis directory; default: run/analysis/<timestamp>")
    compare = commands.add_parser("compare", help="Compare saved accounting reports; first run is baseline")
    compare.add_argument("paths", nargs="+", type=Path, help="Run/analysis directories or accounting JSON files")
    compare.add_argument("--output", type=Path, required=True, help="New comparison directory")
    evaluation = commands.add_parser("evaluate", help="Plan/execute saved evaluation commands or attach a local report")
    evaluation.add_argument("path", type=Path)
    evaluation.add_argument("action", choices=("submit", "fetch", "attach", "local"))
    evaluation.add_argument("--execute", action="store_true", help="Explicitly execute the saved evaluator")
    evaluation.add_argument("--report", type=Path)
    evaluation.add_argument("--run-id")
    args = parser.parse_args()
    if args.command == "run" and not args.dry_run and (args.runtime is None or args.output is None):
        parser.error("execution requires --runtime and --output; use --dry-run for a read-only plan")
    try:
        show_accounting = lambda report: print(render_accounting(report), file=sys.stderr)
        if args.command == "evaluate":
            from .evaluation import evaluate
            result = evaluate(args.path, args.action, execute=args.execute, report=args.report, run_id=args.run_id)
        elif args.command == "analyze":
            from .analysis import analyze_run
            result = analyze_run(args.path, args.output)
            show_accounting(result.pop("report"))
        elif args.command == "compare":
            from .analysis import compare_runs
            result = compare_runs(args.paths, args.output)
            print(result.pop("overhead_text"), file=sys.stderr)
        else:
            experiment = load_experiment(args.path)
        if args.command == "run":
            if args.dry_run:
                from .planning import plan_experiment
                result = plan_experiment(experiment)
            else:
                from .execution import run_experiment
                result = run_experiment(experiment, read_toml(args.runtime), args.output,
                                        on_accounting=show_accounting)
        elif args.command == "resume":
            from .execution import run_experiment
            result = run_experiment(experiment, read_toml(args.runtime), args.output, resume=True,
                                    on_accounting=show_accounting)
        elif args.command == "config":
            result = config_dict(experiment)
    except (ConfigError, OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0
