#!/usr/bin/env python3
"""
Utility functions for downloading and unpacking model vault data from S3.
"""

import os
import subprocess
import tempfile
from typing import Optional

import boto3
from botocore.exceptions import ClientError


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
    logger.debug(f"Downloading s3://{bucket}/{key} to {output_path}")

    try:
        s3_client.download_file(bucket, key, output_path)
        logger.debug(f"Successfully downloaded {os.path.basename(output_path)}")
        return True
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.error(f"Failed to download from S3: {error_code} - {str(e)}")
        return False


def unpack_local_file(
    parquet_file: str, output_dir: str, logger, workers: int = None
) -> bool:
    """
    Unpack a local Parquet file using modelvault.

    Args:
        parquet_file: Path to the local Parquet file
        output_dir: Directory to extract files into
        logger: Logger instance
        workers: Number of concurrent workers (optional)

    Returns:
        True if successful, False otherwise
    """
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


def unpack_modelvault(
    s3_uri: str, output_dir: str, logger, workers: int = None
) -> bool:
    """
    Fetch a Parquet model vault from S3 and unpack it.

    Args:
        s3_uri: S3 URI (e.g., s3://bucket/path/to/file.parquet)
        output_dir: Directory to extract files into
        logger: Logger instance
        workers: Number of concurrent workers (optional)

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
    logger.info(f"DATA       | modelvault: from S3: bucket={bucket}, key={key}")

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
        success = unpack_local_file(temp_file, output_dir, logger, workers)
        if not success:
            return False

        # Fix ownership and permissions on all extracted files
        # HMS runs as uid 1000, so we need to chown files to that user
        logger.debug("Setting ownership and permissions on extracted files")
        try:
            # First, fix the output directory itself
            os.chown(output_dir, 1000, 1000)
            os.chmod(output_dir, 0o755)  # rwxr-xr-x

            # Then walk through all subdirectories and files
            for root, dirs, files in os.walk(output_dir):
                # Change ownership to uid 1000 (hms user in headless container)
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    os.chown(dir_path, 1000, 1000)
                    os.chmod(dir_path, 0o755)  # rwxr-xr-x
                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    os.chown(file_path, 1000, 1000)
                    os.chmod(file_path, 0o644)  # rw-r--r--
            logger.debug("Successfully set ownership and permissions on all files")
        except OSError as e:
            logger.warning(f"Failed to set ownership/permissions: {e}")
            # Don't fail the entire operation for permission issues

        return True

    finally:
        # Clean up temporary file
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
                logger.debug(f"Cleaned up temporary file: {temp_file}")
            except OSError as e:
                logger.warning(f"Failed to remove temporary file {temp_file}: {e}")
