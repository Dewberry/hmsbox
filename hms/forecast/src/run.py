#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
from pathlib import Path

from forecast_time import ensure_control_file_from_map
from forecast_logging import setup_json_logging

__version__ = "0.1.0"

logger = setup_json_logging()


def run_cmd(command: list[str], error_msg: str, step: str, json_logs_only: bool) -> int:
    """Run a subprocess command and handle output."""
    try:
        if json_logs_only:
            # Filter output to only show JSON lines
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            # Print only lines that look like JSON
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    print(line, file=sys.stdout)
            return result.returncode
        else:
            # Show all output
            result = subprocess.run(command)
            return result.returncode
    except Exception as e:
        logger.error(f"{error_msg} [step={step}]: {e}")
        return 1


def _resolve_python_command(
    python_bin: Path, python_args: list[str]
) -> tuple[list[str] | None, str]:
    if not python_args:
        return None, ""

    command = python_args[0].strip().lower()
    if command in {"parse-results-stats", "parse_results_stats"}:
        return [
            str(python_bin),
            "/usr/local/bin/parse_results_stats.py",
            *python_args[1:],
        ], "results_stats"

    return [str(python_bin), "-m", "converter.main", *python_args], "python_converter"


def _run_hms(hms_args: list[str], json_logs_only: bool) -> int:
    logger.debug("Running HMS entrypoint [step=hms]")

    control_status = ensure_control_file_from_map(hms_args, logger)
    if control_status != 0:
        return control_status

    hms_cmd = ["/usr/local/bin/run-hms.sh", *hms_args]
    exit_code = run_cmd(hms_cmd, "HMS entrypoint failed", "hms", json_logs_only)

    if exit_code != 0:
        return exit_code

    logger.debug("HMS entrypoint completed successfully [step=hms]")

    return 0


def _run_python(python_args: list[str], json_logs_only: bool) -> int:
    logger.debug("Running Python entrypoint [step=python]")

    os.chdir("/app")
    python_bin = Path("/app/.venv/bin/python")
    if not python_bin.exists():
        logger.error(
            "Python step failed [step=python] [exit_code=127]: /app/.venv/bin/python was not found"
        )
        return 127

    command_args = list(python_args)
    if command_args and command_args[0] == "--":
        command_args = command_args[1:]

    command, mode = _resolve_python_command(python_bin, command_args)
    if command is None:
        logger.info("No python args provided; skipping Python step [step=python]")
        return 0

    step = f"python_{mode}"
    error_message = (
        "Python converter failed"
        if mode == "python_converter"
        else "Results stats parser failed"
    )
    exit_code = run_cmd(command, error_message, step, json_logs_only)
    if exit_code != 0:
        return exit_code

    if mode == "python_converter":
        logger.debug(f"Python converter completed successfully [step={step}]")
    else:
        logger.debug(f"Results stats parser completed successfully [step={step}]")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run HMS and DSS Python converter in sequence"
    )
    parser.add_argument("--hms-only", action="store_true", help="Run only HMS")
    parser.add_argument(
        "--python-only", action="store_true", help="Run only Python converter"
    )
    parser.add_argument(
        "--json-logs-only",
        dest="json_logs_only",
        action="store_true",
        help="Show only JSON-formatted child logs (default)",
    )
    parser.add_argument(
        "--no-json-logs-only",
        dest="json_logs_only",
        action="store_false",
        help="Show raw child logs including non-JSON lines",
    )
    parser.set_defaults(json_logs_only=True)
    parser.add_argument(
        "--python-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Args after this flag run either converter.main or parse_results_stats.py",
    )
    parser.add_argument(
        "hms_args",
        nargs="*",
        help="Positional arguments forwarded to /usr/local/bin/run-hms.sh",
    )
    return parser.parse_args()


def main() -> int:
    os.environ["PATH"] = f"/app/.venv/bin:{os.environ.get('PATH', '')}"

    args = parse_args()
    json_logs_only = args.json_logs_only
    run_hms = not args.python_only
    run_python = not args.hms_only

    if args.hms_only and args.python_only:
        logger.error(
            "--hms-only and --python-only cannot be used together [exit_code=2]"
        )
        return 2

    logger.debug("HMS Forecast Container starting")

    if run_hms:
        hms_status = _run_hms(args.hms_args, json_logs_only)
        if hms_status != 0:
            return hms_status

    if run_python:
        python_status = _run_python(args.python_args, json_logs_only)
        if python_status != 0:
            return python_status

    logger.info("All entrypoints completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
