#!/usr/bin/env python3

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_time_input(value: str) -> datetime:
    if len(value) == 10:
        dt = datetime.strptime(value, "%Y-%m-%d")
        return dt.replace(hour=1, minute=0, second=0, tzinfo=timezone.utc)

    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def infer_windows(lookback_start: datetime) -> dict[str, dict[str, datetime]]:
    lookback_end = lookback_start + timedelta(hours=335)
    forecast_start = lookback_end + timedelta(hours=1)
    forecast_end = forecast_start + timedelta(hours=17)
    return {
        "lookback": {"start": lookback_start, "end": lookback_end},
        "forecast": {"start": forecast_start, "end": forecast_end},
    }


def infer_windows_from_forecast_start(
    forecast_start: datetime,
) -> dict[str, dict[str, datetime]]:
    lookback_end = forecast_start - timedelta(hours=1)
    lookback_start = lookback_end - timedelta(hours=335)
    forecast_end = forecast_start + timedelta(hours=17)
    return {
        "lookback": {"start": lookback_start, "end": lookback_end},
        "forecast": {"start": forecast_start, "end": forecast_end},
    }


def write_control_file(
    control_type: str, start_dt: datetime, end_dt: datetime, output_dir: Path
) -> Path:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise PermissionError(
            f"Cannot create directory {output_dir}: {e}. "
            f"Ensure the mounted model directory has write permissions for the container user (uid={os.getuid()})."
        ) from e

    output_file = output_dir / f"{control_type}.control"

    now = datetime.now(timezone.utc)
    content = (
        f"Control: {control_type}\n"
        f"     Last Modified Date: {now.strftime('%d %B %Y')}\n"
        f"     Last Modified Time: {now.strftime('%H:%M:%S')}\n"
        "     Version: 4.14\n"
        f"     Start Date: {start_dt.strftime('%d %B %Y')}\n"
        f"     Start Time: {start_dt.strftime('%H:%M')}\n"
        f"     End Date: {end_dt.strftime('%d %B %Y')}\n"
        f"     End Time: {end_dt.strftime('%H:%M')}\n"
        "     Time Interval: 60\n"
        "End:\n"
    )

    try:
        output_file.write_text(content, encoding="utf-8")
        # Fix permissions for HMS user (uid 1000)
        # This allows HMS to run as the hms user and still access the file
        try:
            os.chown(output_file, 1000, 1000)
            os.chmod(output_file, 0o644)  # rw-r--r--
        except (OSError, PermissionError):
            # Not critical if permission change fails (e.g., running as non-root in dev)
            pass
    except PermissionError as e:
        raise PermissionError(
            f"Cannot write to {output_file}: {e}. "
            f"Ensure the mounted model directory has write permissions for the container user (uid={os.getuid()})."
        ) from e

    return output_file


def ensure_control_file_from_map(hms_args: list[str], logger: logging.Logger) -> int:
    raw_map = os.environ.get("params", "").strip()
    if not raw_map:
        return 0

    if len(hms_args) < 2:
        logger.error(
            "`params` was provided but HMS arguments are incomplete [step=control_file] [required_args=<hms_model_path> <simulation_name>]"
        )
        return 2

    try:
        time_map = json.loads(raw_map)
    except json.JSONDecodeError as exc:
        logger.error(f"Invalid `params` JSON [step=control_file] [error={exc}]")
        return 2

    lookback_start = str(time_map.get("lookback_start", "")).strip()
    forecast_start = str(time_map.get("forecast_start", "")).strip()

    if lookback_start and forecast_start:
        logger.error(
            "`params` must provide only one of 'lookback_start' or 'forecast_start' [step=control_file]"
        )
        return 2

    if not lookback_start and not forecast_start:
        logger.error(
            "`params` missing required key: provide 'lookback_start' or 'forecast_start' [step=control_file]"
        )
        return 2

    if lookback_start:
        try:
            lookback_start_dt = parse_time_input(lookback_start)
        except ValueError as exc:
            logger.error(
                f"Invalid lookback_start value [step=control_file] [lookback_start={lookback_start}] [error={exc}]"
            )
            return 2
        windows = infer_windows(lookback_start_dt)
    else:
        try:
            forecast_start_dt = parse_time_input(forecast_start)
        except ValueError as exc:
            logger.error(
                f"Invalid forecast_start value [step=control_file] [forecast_start={forecast_start}] [error={exc}]"
            )
            return 2
        windows = infer_windows_from_forecast_start(forecast_start_dt)

    simulation_name = hms_args[1].strip().lower()
    control_type_override = str(time_map.get("control_type", "")).strip().lower()
    hms_model_path = Path(hms_args[0])
    output_dir = hms_model_path.parent

    # Keep both control specs present so HMS does not remove a run because its
    # control file is missing when opening the project.
    try:
        lookback_control_path = write_control_file(
            control_type="Lookback",
            start_dt=windows["lookback"]["start"],
            end_dt=windows["lookback"]["end"],
            output_dir=output_dir,
        )
        forecast_control_path = write_control_file(
            control_type="Forecast",
            start_dt=windows["forecast"]["start"],
            end_dt=windows["forecast"]["end"],
            output_dir=output_dir,
        )
    except PermissionError as e:
        logger.error(
            f"Permission denied writing control files [step=control_file]: {e}"
        )
        return 13  # EACCES

    if control_type_override in {"lookback", "forecast"}:
        control_type = "Lookback" if control_type_override == "lookback" else "Forecast"
    elif simulation_name == "lookback":
        control_type = "Lookback"
    else:
        control_type = "Forecast"

    control_file = str(
        lookback_control_path if control_type == "Lookback" else forecast_control_path
    )

    if control_type == "Forecast":
        logger.debug(
            f"Control file generated from `params` [step=control_file] "
            f"[control_file={control_file}] "
            f"[forecast_control_file={forecast_control_path}] "
        )
    elif control_type == "Lookback":
        logger.debug(
            f"Control file generated from `params` [step=control_file] "
            f"[control_file={control_file}] "
            f"[lookback_control_file={lookback_control_path}] "
        )
    return 0
