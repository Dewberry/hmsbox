#!/usr/bin/env python3
"""
Utility functions for downloading and unpacking model vault data from S3.
"""

import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
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


def extract_zip(zip_file: str, output_dir: str, logger) -> bool:
    """Extract a .zip, rejecting paths that escape the output directory."""
    logger.info(f"Extracting: {zip_file} -> {output_dir}")
    root = Path(output_dir).resolve()
    try:
        with zipfile.ZipFile(zip_file) as zf:
            for member in zf.infolist():
                try:
                    member_path = Path(member.filename)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError("Unsafe member path")
                    (root / member.filename).resolve().relative_to(root)
                except ValueError:
                    logger.error(f"Unsafe path in zip archive {zip_file}: {member}")
                    return False
            zf.extractall(root)
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        logger.error(f"Failed to extract: {zip_file} -> {output_dir}")
        return False
    logger.info(f"Successfully extracted: {zip_file} -> {output_dir}")
    return True


def unpack_local_file(
    input_file: str, output_dir: str, logger, workers: int = None
) -> bool:
    """
    Unpack a local Parquet file using modelvault, or extract a local .zip file.

    Args:
        input_file: Path to the local archive file
        output_dir: Directory to extract files into
        logger: Logger instance
        workers: Number of concurrent workers. Only applied to Parquet archives. (optional)

    Returns:
        True if successful, False otherwise
    """
    if not os.path.exists(input_file):
        logger.error(f"Archive file not found: {input_file}")
        return False

    if not os.path.isfile(input_file):
        logger.error(f"Path is not a file: {input_file}")
        return False

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    if zipfile.is_zipfile(input_file):
        return extract_zip(input_file, output_dir, logger)

    # Build modelvault command
    cmd = [
        "modelvault",
        "unpack",
        "--input-file",
        input_file,
        "--output-dir",
        output_dir,
    ]

    if workers:
        cmd.extend(["--workers", str(workers)])

    logger.debug(f"Unpacking local file: {input_file}")
    logger.debug(f"Output directory: {output_dir}")

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.debug(f"Successfully unpacked {input_file}")
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
    Fetch a model vault (SOZip/zip or Parquet) from S3 and unpack it.

    Args:
        s3_uri: S3 URI (e.g., s3://bucket/path/to/model.zip)
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
