#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from vault_logging import setup_json_logging


def unpack_local_file(parquet_file: str, output_dir: str, workers: int = None) -> bool:
    """
    Unpack a local Parquet file using modelvault.

    Args:
        parquet_file: Path to the local Parquet file
        output_dir: Directory to extract files into
        workers: Number of concurrent workers (optional)

    Returns:
        True if successful, False otherwise
    """
    logger = setup_json_logging()

    if not os.path.exists(parquet_file):
        logger.error(f"Parquet file not found: {parquet_file}")
        return False

    if not os.path.isfile(parquet_file):
        logger.error(f"Path is not a file: {parquet_file}")
        return False

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Build modelvault command
    cmd = [
        "modelvault",
        "unpack",
        "--input-file",
        parquet_file,
        "--output-dir",
        output_dir,
    ]

    if workers:
        cmd.extend(["--workers", str(workers)])

    logger.debug(f"Unpacking local file: {parquet_file}")
    logger.debug(f"Output directory: {output_dir}")

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.debug(f"Successfully unpacked {parquet_file}")
        if result.stdout:
            logger.debug(f"modelvault output: {result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"modelvault unpack failed with exit code {e.returncode}")
        if e.stdout:
            logger.error(f"stdout: {e.stdout}")
        if e.stderr:
            logger.error(f"stderr: {e.stderr}")
        return False
    except FileNotFoundError:
        logger.error(
            "modelvault command not found. Ensure it is installed in the container."
        )
        return False


def download_s3_file(s3_uri: str, output_path: str, logger) -> bool:
    """
    Download a file from S3 to a local path.

    Args:
        s3_uri: S3 URI (e.g., s3://bucket/path/to/file.ext)
        output_path: Local file path to save to
        logger: Logger instance

    Returns:
        True if successful, False otherwise
    """
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

    # Create output directory if needed
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    s3_client = boto3.client("s3")
    logger.info(f"Downloading s3://{bucket}/{key} to {output_path}")

    try:
        s3_client.download_file(bucket, key, output_path)
        logger.debug(f"Successfully downloaded {os.path.basename(output_path)}")
        return True
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.error(f"Failed to download from S3: {error_code} - {str(e)}")
        return False


def fetch_and_unpack(
    s3_uri: str,
    output_dir: str,
    workers: int = None,
    forcing_uris: list = None,
    observations_uris: list = None,
) -> bool:
    """
    Fetch a Parquet file from S3 and unpack it, optionally downloading forcing and observations data.

    Args:
        s3_uri: S3 URI (e.g., s3://bucket/path/to/file.parquet)
        output_dir: Directory to extract files into
        workers: Number of concurrent workers (optional)
        forcing_uris: List of S3 URIs for forcing data files (optional)
        observations_uris: List of S3 URIs for observations data files (optional)

    Returns:
        True if successful, False otherwise
    """
    logger = setup_json_logging()

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
    logger.info(f"Fetching model vault from S3: bucket={bucket}, key={key}")

    # Create temporary file for download
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        temp_file = tmp.name

    try:
        # Download from S3
        s3_client = boto3.client("s3")
        logger.debug(f"Downloading s3://{bucket}/{key} to temporary file")

        try:
            s3_client.download_file(bucket, key, temp_file)
            logger.debug(f"Successfully downloaded model vault from S3")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            logger.error(f"Failed to download from S3: {error_code} - {str(e)}")
            return False

        # Unpack the downloaded file
        success = unpack_local_file(temp_file, output_dir, workers)
        if not success:
            return False

        # Download forcing data if provided - store in forcing/ subdirectory
        if forcing_uris:
            logger.debug(f"Downloading {len(forcing_uris)} forcing data file(s)")
            forcing_dir = os.path.join(output_dir, "forcing")
            for forcing_uri in forcing_uris:
                filename = os.path.basename(
                    forcing_uri.split("?")[0]
                )  # Remove query params if any
                output_path = os.path.join(forcing_dir, filename)
                if not download_s3_file(forcing_uri, output_path, logger):
                    logger.error(f"Failed to download forcing file: {forcing_uri}")
                    return False

        # Download observations data if provided - store in observations/ subdirectory
        if observations_uris:
            logger.debug(
                f"Downloading {len(observations_uris)} observations data file(s)"
            )
            observations_dir = os.path.join(output_dir, "observations")
            for obs_uri in observations_uris:
                filename = os.path.basename(
                    obs_uri.split("?")[0]
                )  # Remove query params if any
                output_path = os.path.join(observations_dir, filename)
                if not download_s3_file(obs_uri, output_path, logger):
                    logger.error(f"Failed to download observations file: {obs_uri}")
                    return False

        return True

    finally:
        # Clean up temporary file
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
                logger.debug(f"Cleaned up temporary file: {temp_file}")
            except OSError as e:
                logger.warning(f"Failed to remove temporary file {temp_file}: {e}")


def main():
    """Main entry point for vault operations."""
    parser = argparse.ArgumentParser(
        description="Vault pack/unpack utility for model archives",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
        Examples:
        # Unpack a local file
        vault.py unpack /path/to/model.parquet /output/dir

        # Fetch from S3 and unpack
        vault.py fetch-and-unpack s3://bucket/path/to/model.parquet /output/dir

        # With custom worker count
        vault.py unpack /path/to/model.parquet /output/dir --workers 16
                """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Unpack subcommand
    unpack_parser = subparsers.add_parser("unpack", help="Unpack a local Parquet file")
    unpack_parser.add_argument("input_file", help="Path to the Parquet file")
    unpack_parser.add_argument("output_dir", help="Directory to extract files into")
    unpack_parser.add_argument(
        "--workers", type=int, help="Number of concurrent file writers (optional)"
    )

    # Fetch-and-unpack subcommand
    fetch_parser = subparsers.add_parser(
        "fetch-and-unpack", help="Fetch from S3 and unpack"
    )
    fetch_parser.add_argument(
        "s3_uri", help="S3 URI (e.g., s3://bucket/path/to/file.parquet)"
    )
    fetch_parser.add_argument(
        "output_dir", help="Directory to extract files into", nargs="?"
    )
    fetch_parser.add_argument(
        "--workers", type=int, help="Number of concurrent file writers (optional)"
    )
    fetch_parser.add_argument(
        "--forcing",
        action="append",
        help="S3 URI for forcing data file (can be specified multiple times)",
    )
    fetch_parser.add_argument(
        "--observations",
        action="append",
        help="S3 URI for observations data file (can be specified multiple times)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Route to appropriate function
    if args.command == "unpack":
        success = unpack_local_file(args.input_file, args.output_dir, args.workers)
    elif args.command == "fetch-and-unpack":
        # If output_dir is not provided, use /mnt/data/unpacked
        output_dir = args.output_dir or "/mnt/data/unpacked"
        success = fetch_and_unpack(
            args.s3_uri,
            output_dir,
            args.workers,
            forcing_uris=args.forcing,
            observations_uris=args.observations,
        )
    else:
        parser.print_help()
        return 1

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
