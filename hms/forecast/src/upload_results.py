#!/usr/bin/env python3
"""
Upload forecast results (lookback stats and forecast output) to S3.
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import boto3
import yaml
from botocore.exceptions import ClientError
from forecast_logging import setup_json_logging


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
        logger.debug(f"Using S3 bucket from runtime environment: {s3_bucket}")
    else:
        # Build-time substitution was done, config is ready to use
        logger.debug("Using S3 bucket configured at build time")

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
        logger.debug(
            f"METADATA   | mode: '{mode}' year={values['year']} month={values['month']} "
            f"day={values['day']} hour={values['hour']}"
        )
        if description:
            logger.debug(f"METADATA   | description: {description}")
    else:
        # Use current UTC time
        now = datetime.now(timezone.utc)
        values = {
            "year": now.year,
            "month": now.month,
            "day": now.day,
            "hour": now.hour,
        }
        logger.debug(
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
        Dictionary with constructed S3 paths for results
    """
    s3_paths = config["s3_paths"]

    # Create template variables
    template_vars = {
        "year": dt_values["year"],
        "month": dt_values["month"],
        "day": dt_values["day"],
        "hour": dt_values["hour"],
    }

    # Construct result paths
    paths = {
        "lookback_stats": s3_paths["results"]["lookback_stats"].format(**template_vars),
        "forecast_output": s3_paths["results"]["forecast_output"].format(
            **template_vars
        ),
        "lookback_output": s3_paths["results"]["lookback_output"].format(
            **template_vars
        ),
    }

    return paths


def upload_s3_file(local_path: str, s3_uri: str, logger) -> bool:
    """
    Upload a file from local path to S3.

    Args:
        local_path: Local file path to upload from
        s3_uri: S3 URI (e.g., s3://bucket/path/to/file.ext)
        logger: Logger instance

    Returns:
        True if successful, False otherwise
    """
    # Check if local file exists
    if not os.path.exists(local_path):
        logger.error(f"Local file not found: {local_path}")
        return False

    # Parse S3 URI
    if not s3_uri.startswith("s3://"):
        logger.error(f"Invalid S3 URI format: {s3_uri}. Expected s3://bucket/key")
        return False

    s3_path = s3_uri[5:]  # Remove 's3://'
    parts = s3_path.split("/", 1)
    if len(parts) != 2:
        logger.error(f"Invalid S3 path format: {s3_uri}")
        return False

    bucket, key = parts

    s3_client = boto3.client("s3")
    logger.debug(f"Uploading {local_path} to s3://{bucket}/{key}")

    try:
        s3_client.upload_file(local_path, bucket, key)
        file_size = os.path.getsize(local_path)
        logger.debug(
            f"Successfully uploaded {os.path.basename(local_path)} ({file_size} bytes)"
        )
        return True
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.error(f"Failed to upload to S3: {error_code} - {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error uploading to S3: {str(e)}")
        return False


def upload_all_results(s3_paths: Dict[str, str], config: Dict) -> bool:
    """
    Upload all result files to S3 (lookback stats and forecast output).

    Args:
        s3_paths: Dictionary of S3 URIs
        config: Configuration dictionary

    Returns:
        True if successful, False otherwise
    """
    logger = setup_json_logging()

    local_paths = config.get("local_paths", {})
    model_dir = local_paths.get("model_dir", "/mnt/model")
    results_dir = os.path.join(model_dir, "results")

    # Define local result files
    result_files = [
        ("stats.parquet", "lookback_stats"),
        ("forecast.parquet", "forecast_output"),
        ("lookback.parquet", "lookback_output"),
    ]

    all_success = True
    for filename, s3_key in result_files:
        local_path = os.path.join(results_dir, filename)

        if not os.path.exists(local_path):
            logger.warning(f"Result file not found, skipping: {local_path}")
            continue
        logger.info(f"DATA       | uploading {s3_paths[s3_key]}")
        if not upload_s3_file(local_path, s3_paths[s3_key], logger):
            logger.error(f"Failed to upload result file: {filename}")
            all_success = False
        else:
            logger.debug(f"{filename} uploaded successfully")

    if all_success:
        logger.debug("All results uploaded successfully")
    else:
        logger.error("Some results failed to upload")

    return all_success


def main():
    """Main entry point for results upload."""
    parser = argparse.ArgumentParser(
        description="Upload HMS forecast results to S3",
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
        type=str,
        default="/app/config.yaml",
        help="Path to configuration YAML file (default: /app/config.yaml)",
    )

    args = parser.parse_args()

    logger = setup_json_logging()
    logger.debug("Starting results upload to S3")

    # Load configuration
    config = load_config(args.config)

    # Get datetime values
    dt_values = get_datetime_values(args.mode, config)

    # Construct S3 paths
    s3_paths = construct_s3_paths(config, dt_values)

    # Upload results
    success = upload_all_results(s3_paths, config)

    if success:
        logger.debug("Results upload completed successfully")
        return 0
    else:
        logger.debug("Results upload failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
