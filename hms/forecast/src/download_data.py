#!/usr/bin/env python3
"""
Download model vault, forcing data, and observations from S3 for HMS forecast.
"""

import argparse
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import boto3
import yaml
from botocore.exceptions import ClientError
from forecast_logging import setup_json_logging
from vault_utils import download_s3_file, unpack_modelvault

# Import parquet_to_dss converter
try:
    from dss_parquet import parquet_to_dss
except ModuleNotFoundError:
    # Fallback if running outside containerized environment
    import sys

    sys.path.insert(0, "/app/src")
    from dss_parquet import parquet_to_dss


def _replace_env_vars(obj, env_vars: Dict[str, str]):
    """Recursively replace environment variable placeholders in config."""
    if isinstance(obj, dict):
        return {k: _replace_env_vars(v, env_vars) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_replace_env_vars(item, env_vars) for item in obj]
    elif isinstance(obj, str):
        # Replace {S3_BUCKET} and other env var placeholders
        result = obj
        for key, value in env_vars.items():
            result = result.replace(f"{{{key}}}", value)
        return result
    else:
        return obj


def load_config(config_path: str = "/app/config.yaml") -> Dict:
    """Load configuration from YAML file with environment variable substitution."""
    logger = setup_json_logging()

    if not os.path.exists(config_path):
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Get environment variables for substitution
    # Check if config still has {S3_BUCKET} placeholder (not substituted at build time)
    config_str = yaml.dump(config)
    if "{S3_BUCKET}" in config_str:
        # Build-time substitution was not done, require runtime env var
        s3_bucket = os.environ.get("HMS_S3_BUCKET")
        if not s3_bucket:
            logger.error(
                "HMS_S3_BUCKET environment variable is required but not set (S3 bucket was not configured at build time)"
            )
            sys.exit(1)

        env_vars = {
            "S3_BUCKET": s3_bucket,
        }
        # Replace environment variable placeholders
        config = _replace_env_vars(config, env_vars)
        logger.info(f"Using S3 bucket from runtime environment: {s3_bucket}")
    else:
        # Build-time substitution was done, config is ready to use
        logger.info("Using S3 bucket configured at build time")

    return config


def get_datetime_values(mode: Optional[str], config: Dict) -> Dict[str, int]:
    """
    Get year, month, day, hour values from specified mode or current time.

    Args:
        mode: Mode name (test, validation1, validation2, validation3) or None for current time
        config: Configuration dictionary

    Returns:
        Dictionary with year, month, day, hour keys
    """
    logger = setup_json_logging()

    if mode:
        # Look up mode in config
        modes = config.get("modes", {})
        if mode not in modes:
            available = ", ".join(modes.keys()) if modes else "none"
            logger.error(
                f"Mode '{mode}' not found in config. Available modes: {available}"
            )
            sys.exit(1)

        mode_config = modes[mode]
        values = {
            "year": mode_config["year"],
            "month": mode_config["month"],
            "day": mode_config["day"],
            "hour": mode_config["hour"],
        }
        description = mode_config.get("description", "")
        logger.info(
            f"METADATA   | mode: '{mode}' year={values['year']} month={values['month']} "
            f"day={values['day']} hour={values['hour']}"
        )
        if description:
            logger.info(f"METADATA   | description: {description}")
    else:
        # Use current UTC time
        now = datetime.now(timezone.utc)
        values = {
            "year": now.year,
            "month": now.month,
            "day": now.day,
            "hour": now.hour,
        }
        logger.info(
            f"METADATA   | mode: 'operational' year={values['year']} month={values['month']} "
            f"day={values['day']} hour={values['hour']}"
        )

    return values


def construct_s3_paths(config: Dict, dt_values: Dict[str, int]) -> Dict[str, str]:
    """
    Construct S3 paths by replacing template variables.

    Args:
        config: Configuration dictionary
        dt_values: Dictionary with year, month, day, hour values

    Returns:
        Dictionary with constructed S3 paths
    """
    model_version = config.get("model_version", "trinity-v20260509")
    s3_paths = config["s3_paths"]

    # Create template variables
    template_vars = {
        "model_version": model_version,
        "year": dt_values["year"],
        "month": dt_values["month"],
        "day": dt_values["day"],
        "hour": dt_values["hour"],
    }

    # Construct paths
    paths = {
        "model_vault": s3_paths["model_vault"].format(**template_vars),
        "forcing_qpf": s3_paths["forcing"]["qpf"].format(**template_vars),
        "forcing_temp": s3_paths["forcing"]["temp"].format(**template_vars),
        "forcing_qpe": s3_paths["forcing"]["qpe"].format(**template_vars),
        "observations": s3_paths["observations"].format(**template_vars),
    }

    return paths


def download_all_data(
    s3_paths: Dict[str, str], config: Dict, skip_model: bool = False
) -> bool:
    """
    Download all data from S3 (model vault, forcing, observations).

    Args:
        s3_paths: Dictionary of S3 URIs
        config: Configuration dictionary
        skip_model: If True, skip downloading model vault

    Returns:
        True if successful, False otherwise
    """
    logger = setup_json_logging()

    local_paths = config.get("local_paths", {})
    model_dir = local_paths.get("model_dir", "/mnt/model")
    forcing_dir = local_paths.get("forcing_dir", "/mnt/model/forcing")
    observations_dir = local_paths.get("observations_dir", "/mnt/model/observations")

    # Create directories
    os.makedirs(model_dir, exist_ok=True)
    os.makedirs(forcing_dir, exist_ok=True)
    os.makedirs(observations_dir, exist_ok=True)

    # Download and unpack model vault
    if not skip_model:
        logger.info(f"MODEL      | source: {s3_paths['model_vault']}")
        success = unpack_modelvault(s3_paths["model_vault"], model_dir, logger)
        if not success:
            logger.error("Failed to download and unpack model vault")
            return False
        logger.debug("DATA       | modelvault downloaded and unpacked successfully")

        # Ensure HMS subdirectories exist — modelvault skips empty dirs,
        # but HMS needs these to write lock files, state, and results.
        hms_subdirs = [
            "results",
            "basinStates",
            "forecast",
            "datavar",
            "ensemble",
            "montecarlo",
            "optimizer",
            "frequency",
        ]
        for subdir in hms_subdirs:
            os.makedirs(os.path.join(model_dir, subdir), exist_ok=True)
        logger.debug("DATA       | HMS subdirectories ensured")
    else:
        logger.info("DATA       | modelvault download skipped (--skip-model flag)")

    # Download forcing data
    forcing_files = [
        ("hrrr_qpf.nc", s3_paths["forcing_qpf"]),
        ("rtma_temp.nc", s3_paths["forcing_temp"]),
        ("mrms_qpe.nc", s3_paths["forcing_qpe"]),
    ]

    for filename, s3_uri in forcing_files:
        output_path = os.path.join(forcing_dir, filename)
        logger.info(f"DATA       | forcing: {s3_uri}")
        if not download_s3_file(s3_uri, output_path, logger):
            logger.error(f"Failed to download forcing file: {filename}")
            return False
        logger.debug(f"Successfully downloaded {filename}")

    # Download observations and convert to DSS if needed
    obs_parquet_filename = "gages.parquet"
    obs_dss_filename = "gages.dss"
    obs_parquet_path = os.path.join(observations_dir, obs_parquet_filename)
    obs_dss_path = os.path.join(observations_dir, obs_dss_filename)

    # Check if DSS observations already exist locally
    if os.path.exists(obs_dss_path):
        logger.info(
            f"DATA       | observations DSS file already exists at {obs_dss_path}, skipping download and conversion"
        )
        logger.debug("All data downloaded successfully")
        return True

    logger.info(f"DATA       | observations: {s3_paths['observations']}")

    # Detect if S3 observations are already in DSS format or parquet
    obs_s3_path = s3_paths["observations"]
    if obs_s3_path.endswith(".dss"):
        # S3 observations are already DSS, download directly as DSS
        if not download_s3_file(obs_s3_path, obs_dss_path, logger):
            logger.error("Failed to download observations DSS file")
            return False
        logger.debug("Successfully downloaded observations DSS file")
    else:
        # S3 observations are parquet, download and convert to DSS
        if not download_s3_file(obs_s3_path, obs_parquet_path, logger):
            logger.error("Failed to download observations parquet file")
            return False
        logger.debug("Successfully downloaded observations parquet file")

        # Convert parquet to DSS format
        logger.info("CONVERTER  | Converting observations from parquet to DSS format")
        try:
            result = parquet_to_dss(
                parquet_path=obs_parquet_path,
                output_dss_path=obs_dss_path,
                suppress_dss_output=False,
            )
            if "error" in result:
                logger.error(f"Failed to convert parquet to DSS: {result['error']}")
                return False
            logger.info(
                f"CONVERTER  | Successfully converted {result.get('converted', 0)} timeseries to DSS"
            )
        except Exception as e:
            logger.error(
                f"Failed to convert observations parquet to DSS: {e}", exc_info=True
            )
            return False

    logger.debug("All data downloaded successfully")
    return True


def main():
    """Main entry point for data download."""
    parser = argparse.ArgumentParser(
        description="Download HMS forecast model and data from S3",
        epilog="Available modes: test, validation1, validation2, validation3",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["test", "validation1", "validation2", "validation3"],
        help="Use predefined datetime mode (test, validation1, validation2, validation3). If not specified, uses current time.",
    )
    parser.add_argument(
        "--config",
        default="/app/config.yaml",
        help="Path to configuration file (default: /app/config.yaml)",
    )
    parser.add_argument(
        "--skip-model",
        action="store_true",
        help="Skip downloading model vault (useful if model is already present)",
    )

    args = parser.parse_args()

    logger = setup_json_logging()
    logger.debug("Starting data download [step=download_data]")

    # Load configuration
    config = load_config(args.config)

    # Get datetime values
    dt_values = get_datetime_values(args.mode, config)

    # Construct S3 paths
    s3_paths = construct_s3_paths(config, dt_values)

    # Log the paths we'll be downloading from
    logger.debug(f"Model vault: {s3_paths['model_vault']}")
    logger.debug(f"Forcing QPF: {s3_paths['forcing_qpf']}")
    logger.debug(f"Forcing Temp: {s3_paths['forcing_temp']}")
    logger.debug(f"Forcing QPE: {s3_paths['forcing_qpe']}")
    logger.debug(f"Observations: {s3_paths['observations']}")

    # Download all data
    success = download_all_data(s3_paths, config, args.skip_model)

    if success:
        logger.debug("Data download completed successfully [step=download_data]")
        return 0
    else:
        logger.error("Data download failed [step=download_data]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
