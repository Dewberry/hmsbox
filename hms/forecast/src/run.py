#!/usr/bin/env python3

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from forecast_logging import setup_json_logging
from forecast_time import ensure_control_file_from_map

__version__ = "0.1.0"

_SCRIPT_DIR = Path(__file__).parent

# Logger will be reconfigured in main() based on --debug flag
logger = setup_json_logging()


def _get_observations_format(observations_dir: str) -> str | None:
    """
    Check the format of observations files.
    Returns 'parquet', 'dss', or None if not found.
    """
    parquet_path = Path(observations_dir) / "gages.parquet"
    dss_path = Path(observations_dir) / "gages.dss"

    if parquet_path.exists():
        return "parquet"
    elif dss_path.exists():
        return "dss"
    return None


def _validate_hms_output(model_dir: str, control_name: str) -> bool:
    """
    Validate that HMS simulation generated expected output files.
    Returns True if output found, False otherwise.

    HMS writes RUN_<name>.results (XML) when spatial results are disabled,
    and RUN_<name>.h5 (HDF5) when 'Is Save Spatial Results: Yes' is set.
    Either file is accepted as proof the simulation ran successfully.
    """
    results_dir = Path(model_dir) / "results"
    candidates = [
        results_dir / f"RUN_{control_name}.results",
        results_dir / f"RUN_{control_name}.h5",
    ]

    for results_file in candidates:
        if results_file.exists():
            if results_file.stat().st_size == 0:
                logger.error(
                    f"HMS simulation validation failed: results file is empty at {results_file}"
                )
                return False
            logger.debug(
                f"HMS output validation passed: {results_file} (size: {results_file.stat().st_size} bytes)"
            )
            return True

    logger.error(
        f"HMS simulation validation failed: no results file found in {results_dir} "
        f"(checked: {', '.join(c.name for c in candidates)}). "
        f"This typically indicates HMS failed to run properly. Check: "
        f"1) Model file path is correct, 2) Model is readable, 3) Control file exists"
    )
    return False


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
            str(_SCRIPT_DIR / "parse_results_stats.py"),
            *python_args[1:],
        ], "results_stats"

    return [str(python_bin), "-m", "src.convert", *python_args], "python_converter"


def _run_hms(
    hms_args: list[str], json_logs_only: bool, debug_mode: bool = False
) -> int:
    logger.debug("Running HMS entrypoint [step=hms]")

    control_status = ensure_control_file_from_map(hms_args, logger)
    if control_status != 0:
        return control_status

    # Set environment variables for HMS debug mode
    if debug_mode:
        # Enable debug logging in HMS Java process
        os.environ["JAVA_OPTS"] = (
            "-Dorg.slf4j.simpleLogger.defaultLogLevel=debug -Dlog4j2.statusLoggerLevel=DEBUG"
        )
        logger.debug("HMS debug mode enabled: verbose Java logging active")

    hms_cmd = ["/usr/local/bin/run-hms.sh", *hms_args]
    exit_code = run_cmd(hms_cmd, "HMS entrypoint failed", "hms", json_logs_only)

    if exit_code != 0:
        return exit_code

    logger.debug("HMS entrypoint completed successfully [step=hms]")

    return 0


def _run_python(
    python_args: list[str], json_logs_only: bool, debug_mode: bool = False
) -> int:
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

    # Add --verbose flag for converter commands in debug mode
    if (
        debug_mode
        and command_args
        and command_args[0] not in {"parse-results-stats", "parse_results_stats"}
    ):
        # Only add verbose flag if not already present
        if "--verbose" not in command_args and "-v" not in command_args:
            command_args.insert(1, "--verbose")  # Insert after command name
            logger.debug("Added --verbose flag to converter command")

    command, mode = _resolve_python_command(python_bin, command_args)
    if command is None:
        logger.info(
            "PROCESSING | No processing args provided; skipping Python step [step=python]"
        )
        return 0

    step = f"python_{mode}"
    error_message = (
        "Python converter failed"
        if mode == "python_converter"
        else "Results stats parser failed"
    )

    # Log the command being executed
    logger.debug(f"Executing python command [step={step}]: {' '.join(command)}")

    exit_code = run_cmd(command, error_message, step, json_logs_only)
    if exit_code != 0:
        logger.error(
            f"Python command failed [step={step}] [exit_code={exit_code}]: {' '.join(command)}"
        )
        return exit_code

    if mode == "python_converter":
        logger.debug(f"Python converter completed successfully [step={step}]")
    else:
        logger.debug(f"Results stats parser completed successfully [step={step}]")

    return 0


def _set_params_from_mode(
    mode: str | None, config_path: str = "/app/config.yaml"
) -> None:
    """Set the params environment variable based on mode or config datetime."""
    if not mode:
        return  # No mode specified, params will remain empty

    logger.debug(f"Setting params environment variable from mode: {mode}")

    try:
        if not os.path.exists(config_path):
            logger.warning(
                f"Config file not found: {config_path}, cannot set params from mode"
            )
            return

        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        modes = config.get("modes", {})
        if mode not in modes:
            logger.warning(f"Mode '{mode}' not found in config")
            return

        mode_config = modes[mode]
        year = mode_config.get("year")
        month = mode_config.get("month")
        day = mode_config.get("day")
        hour = mode_config.get("hour")

        if not all([year, month, day, hour is not None]):
            logger.warning(f"Incomplete datetime values in config for mode '{mode}'")
            return

        # Create forecast_start datetime from mode values
        forecast_start = datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)

        # Create params JSON with forecast_start
        params = {
            "forecast_start": forecast_start.isoformat(),
        }

        os.environ["params"] = json.dumps(params)
        logger.debug(f"Set params environment variable: {json.dumps(params)}")

    except Exception as e:
        logger.warning(f"Failed to set params from mode: {e}")


def _run_download(
    mode: str, skip_model: bool, json_logs_only: bool, debug_mode: bool = False
) -> int:
    """Run data download from S3."""
    logger.debug("Running download entrypoint [step=download]")

    download_cmd = ["python3", str(_SCRIPT_DIR / "download_data.py")]
    if mode:
        download_cmd.extend(["--mode", mode])
    if skip_model:
        download_cmd.append("--skip-model")

    # Note: download_data.py uses the same logger, so debug mode is already active
    if debug_mode:
        logger.debug("Download running with debug logging enabled")

    exit_code = run_cmd(
        download_cmd, "Data download failed", "download", json_logs_only
    )

    if exit_code != 0:
        return exit_code

    logger.debug("Download entrypoint completed successfully [step=download]")
    return 0


def _run_upload(mode: str, json_logs_only: bool, debug_mode: bool = False) -> int:
    """Run results upload to S3."""
    logger.debug("Running upload entrypoint [step=upload]")

    upload_cmd = ["python3", str(_SCRIPT_DIR / "upload_results.py")]
    if mode:
        upload_cmd.extend(["--mode", mode])

    # Note: upload_results.py uses the same logger, so debug mode is already active
    if debug_mode:
        logger.debug("Upload running with debug logging enabled")

    exit_code = run_cmd(upload_cmd, "Results upload failed", "upload", json_logs_only)

    if exit_code != 0:
        return exit_code

    logger.debug("Upload entrypoint completed successfully [step=upload]")
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
        "--download",
        dest="download",
        action="store_true",
        default=True,
        help="Download model and data from S3 before running HMS (default: True)",
    )
    parser.add_argument(
        "--no-download",
        dest="download",
        action="store_false",
        help="Disable downloading model and data from S3",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["test", "validation1", "validation2", "validation3"],
        help="Use predefined datetime mode (test, validation1, validation2, validation3). If not specified with --download, uses current time. Requires --download.",
    )
    parser.add_argument(
        "--skip-model",
        action="store_true",
        help="Skip downloading model vault, only download forcing/observations (requires --download)",
    )
    parser.add_argument(
        "--upload",
        dest="upload",
        action="store_true",
        default=None,
        help="Upload results to S3 after forecast completes (default: True for lookback-forecast mode, False for single-run)",
    )
    parser.add_argument(
        "--no-upload",
        dest="upload",
        action="store_false",
        help="Disable uploading results to S3",
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
        "--debug",
        action="store_true",
        help="Enable full debug mode: sets log level to DEBUG and shows all raw output (equivalent to --no-json-logs-only)",
    )
    parser.add_argument(
        "--run-lookback-forecast",
        action="store_true",
        help="Run Lookback simulation followed by Forecast simulation (default: True)",
    )
    parser.add_argument(
        "--single-run",
        action="store_true",
        help="Run only a single simulation (legacy behavior)",
    )
    parser.add_argument(
        "--lookback-control",
        type=str,
        default="Lookback",
        help="Control file name for lookback simulation (default: Lookback)",
    )
    parser.add_argument(
        "--forecast-control",
        type=str,
        default="Forecast",
        help="Control file name for forecast simulation (default: Forecast)",
    )
    parser.add_argument(
        "--lookback-python-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Python args to run after lookback simulation (optional, defaults to: parse-results-stats {model_dir}/results/RUN_Lookback.results {model_dir}/results/stats.parquet)",
    )
    parser.add_argument(
        "--forecast-python-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Python args to run after forecast simulation (optional, defaults to: dss-to-parquet {model_dir}/Forecast.dss -o {model_dir}/results/forecast.parquet)",
    )
    parser.add_argument(
        "--lookback-dss-python-args",
        nargs="*",
        default=None,
        help="Python args to export Lookback DSS after forecast simulation (optional, defaults to: dss-to-parquet {model_dir}/Lookback.dss -o {model_dir}/results/lookback.parquet). Pass empty list to skip.",
    )
    parser.add_argument(
        "--python-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="Args after this flag run either converter.main or parse_results_stats.py (for single-run mode)",
    )
    parser.add_argument(
        "hms_args",
        nargs="*",
        help="Positional arguments forwarded to /usr/local/bin/run-hms.sh (model path for lookback-forecast mode)",
    )
    return parser.parse_args()


def main() -> int:
    os.environ["PATH"] = f"/app/.venv/bin:{os.environ.get('PATH', '')}"

    args = parse_args()

    # Handle debug mode: enable debug logging and raw output
    debug_mode = args.debug
    if debug_mode:
        global logger
        logger = setup_json_logging(level=logging.DEBUG)
        json_logs_only = False
        logger.debug("Debug mode enabled: verbose logging and raw output active")
    else:
        json_logs_only = args.json_logs_only

    run_download = args.download

    # Determine workflow mode: default is lookback-forecast unless single-run is specified
    is_lookback_forecast = not args.single_run

    # Override if explicit flag is set
    if args.run_lookback_forecast:
        is_lookback_forecast = True

    # Determine if upload should run:
    # - Default to True for lookback-forecast mode
    # - Default to False for single-run mode
    # - Can be overridden with --upload or --no-upload
    if args.upload is None:
        run_upload = is_lookback_forecast
    else:
        run_upload = args.upload

    # For single-run mode, use legacy behavior
    run_hms = not args.python_only
    run_python = not args.hms_only

    logger.info(
        "HMS BOX    | forecast container initializing",
        extra={
            "run_download": run_download,
            "run_upload": run_upload,
            "workflow": "lookback-forecast" if is_lookback_forecast else "single-run",
            "run_hms": run_hms if not is_lookback_forecast else True,
            "run_python": run_python if not is_lookback_forecast else True,
        },
    )

    # Validation
    if args.hms_only and args.python_only:
        logger.error(
            "--hms-only and --python-only cannot be used together [exit_code=2]"
        )
        return 2

    if is_lookback_forecast and (args.hms_only or args.python_only):
        logger.error(
            "lookback-forecast mode is incompatible with --hms-only or --python-only [exit_code=2]"
        )
        return 2

    if (args.mode or args.skip_model) and not args.download:
        logger.error("--mode and --skip-model require --download flag [exit_code=2]")
        return 2

    logger.debug("HMS Forecast Container starting")

    # Run download if requested
    if run_download:
        download_status = _run_download(
            args.mode, args.skip_model, json_logs_only, debug_mode
        )
        if download_status != 0:
            return download_status

        # Set params environment variable for control file generation.
        # In test/validation modes, derive from config; in operational mode,
        # use the current UTC hour (matching what download_data.py used for S3 paths).
        if args.mode:
            _set_params_from_mode(args.mode)
        else:
            now = datetime.now(timezone.utc)
            forecast_start = now.replace(minute=0, second=0, microsecond=0)
            os.environ["params"] = json.dumps(
                {"forecast_start": forecast_start.isoformat()}
            )
            logger.debug(
                f"Operational mode: set forecast_start to {forecast_start.isoformat()}"
            )

    # Execute workflow
    if is_lookback_forecast:
        # Lookback-Forecast workflow: run lookback, then forecast
        if not args.hms_args:
            logger.error(
                "lookback-forecast mode requires model path as positional argument [exit_code=2]"
            )
            return 2

        model_path = args.hms_args[0]
        model_dir = str(Path(model_path).parent)

        # Default hardcoded output paths for lookback-forecast workflow
        default_lookback_python_args = [
            "parse-results-stats",
            f"{model_dir}/results/RUN_Lookback.results",
            f"{model_dir}/results/stats.parquet",
        ]
        default_forecast_python_args = [
            "dss-to-parquet",
            f"{model_dir}/Forecast.dss",
            "-o",
            f"{model_dir}/results/forecast.parquet",
        ]

        # Use provided args if specified, otherwise use defaults
        lookback_python_args = (
            args.lookback_python_args
            if args.lookback_python_args
            else default_lookback_python_args
        )
        forecast_python_args = (
            args.forecast_python_args
            if args.forecast_python_args
            else default_forecast_python_args
        )

        logger.debug(f"Model path: {model_path}, Model dir: {model_dir}")
        logger.debug(f"Lookback python args: {lookback_python_args}")
        logger.debug(f"Forecast python args: {forecast_python_args}")

        # Run Lookback simulation
        logger.debug("starting: lookback simulation")
        lookback_hms_args = [model_path, args.lookback_control]
        lookback_hms_status = _run_hms(lookback_hms_args, json_logs_only, debug_mode)
        if lookback_hms_status != 0:
            logger.error(
                f"Lookback simulation failed [exit_code={lookback_hms_status}]"
            )
            return lookback_hms_status
        logger.debug("completed: lookback simulation")

        # Validate that HMS generated output
        if not _validate_hms_output(model_dir, args.lookback_control):
            logger.error(
                f"Lookback simulation validation failed: expected output not generated [exit_code=1]"
            )
            return 1

        # Run Python processing after Lookback
        logger.info("PROCESSING | export: stats from lookback")
        lookback_python_status = _run_python(
            lookback_python_args, json_logs_only, debug_mode
        )
        if lookback_python_status != 0:
            logger.error(
                f"Lookback python processing failed [exit_code={lookback_python_status}]"
            )
            return lookback_python_status
        logger.debug("lookback processing completed")

        # Run Forecast simulation
        logger.debug("starting: forecast simulation")
        forecast_hms_args = [model_path, args.forecast_control]
        forecast_hms_status = _run_hms(forecast_hms_args, json_logs_only, debug_mode)
        if forecast_hms_status != 0:
            logger.error(
                f"Forecast simulation failed [exit_code={forecast_hms_status}]"
            )
            return forecast_hms_status
        logger.debug("completed: forecast simulation")

        # Validate that HMS generated output
        if not _validate_hms_output(model_dir, args.forecast_control):
            logger.error(
                f"Forecast simulation validation failed: expected output not generated [exit_code=1]"
            )
            return 1

        # Run Python processing after Forecast
        logger.info("PROCESSING | export: results from forecast")
        forecast_python_status = _run_python(
            forecast_python_args, json_logs_only, debug_mode
        )
        if forecast_python_status != 0:
            logger.error(
                f"Forecast python processing failed [exit_code={forecast_python_status}]"
            )
            return forecast_python_status
        logger.info("PROCESSING | data conversion python processing completed")

        # Check observations format and convert if needed
        observations_dir = str(Path(model_dir).parent / "observations")
        obs_format = _get_observations_format(observations_dir)

        if obs_format == "parquet":
            logger.info(
                "PROCESSING | observations are in parquet format, converting to DSS"
            )
            obs_parquet_path = Path(observations_dir) / "gages.parquet"
            obs_dss_path = Path(observations_dir) / "gages.dss"
            parquet_to_dss_args = [
                "parquet-to-dss",
                str(obs_parquet_path),
                "-o",
                str(obs_dss_path),
            ]
            obs_conversion_status = _run_python(
                parquet_to_dss_args, json_logs_only, debug_mode
            )
            if obs_conversion_status != 0:
                logger.error(
                    f"Observations parquet to DSS conversion failed [exit_code={obs_conversion_status}]"
                )
                return obs_conversion_status
            logger.debug("observations parquet to DSS conversion completed")
        elif obs_format == "dss":
            logger.debug("observations are already in DSS format, skipping conversion")
        else:
            logger.warning(
                f"observations not found in {observations_dir}, skipping observations conversion"
            )

        # Run Lookback DSS to parquet export
        default_lookback_dss_python_args = [
            "dss-to-parquet",
            f"{model_dir}/Lookback.dss",
            "-o",
            f"{model_dir}/results/lookback.parquet",
        ]
        lookback_dss_python_args = (
            args.lookback_dss_python_args
            if args.lookback_dss_python_args is not None
            else default_lookback_dss_python_args
        )
        if lookback_dss_python_args:
            logger.info("PROCESSING | export: lookback dss to parquet")
            lookback_dss_status = _run_python(
                lookback_dss_python_args, json_logs_only, debug_mode
            )
            if lookback_dss_status != 0:
                logger.error(
                    f"Lookback DSS export failed [exit_code={lookback_dss_status}]"
                )
                return lookback_dss_status
            logger.debug("lookback DSS export completed")

    else:
        # Single-run mode (legacy behavior)
        if run_hms:
            hms_status = _run_hms(args.hms_args, json_logs_only, debug_mode)
            if hms_status != 0:
                return hms_status

        if run_python:
            python_status = _run_python(args.python_args, json_logs_only, debug_mode)
            if python_status != 0:
                return python_status

    # Upload results to S3 if requested
    if run_upload:
        upload_status = _run_upload(args.mode, json_logs_only, debug_mode)
        if upload_status != 0:
            logger.error("Results upload failed, but forecast completed successfully")
            # Don't fail the entire workflow if upload fails
            # return upload_status

    logger.info("HMS BOX    | Forecast container exited successfully")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        if exit_code != 0:
            logger.error(
                f"HMS BOX    | Forecast container exited with error code {exit_code}"
            )
        raise SystemExit(exit_code)
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"HMS BOX    | Unexpected error: {e}")
        raise SystemExit(1)
