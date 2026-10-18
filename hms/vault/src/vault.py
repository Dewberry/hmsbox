#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import boto3
import sozipfile.sozipfile as sozip
from botocore.exceptions import ClientError
from compression import (
    SOZIP_CHUNK_SIZE_KB,
    SOZIP_MIN_DEFLATE_SIZE_KB,
    recommended_compression_method,
)
from vault_logging import setup_json_logging


def unpack_local_file(input_file: str, output_dir: str, workers: int = None) -> bool:
    """
    Unpack a local model vault archive (SOZip/zip, or Parquet via modelvault).

    Args:
        input_file: Path to the local archive file
        output_dir: Directory to extract files into
        workers: Number of concurrent workers for Parquet archives (optional)

    Returns:
        True if successful, False otherwise
    """
    logger = setup_json_logging()

    if not os.path.exists(input_file):
        logger.error(f"Input not found: {input_file}")
        return False

    if not os.path.isfile(input_file):
        logger.error(f"Input is not a file: {input_file}")
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
    Fetch a model archive (Parquet vault or .zip) from S3 and unpack it, optionally downloading forcing and observations data.

    Args:
        s3_uri: S3 URI (e.g., s3://bucket/path/to/file.parquet)
        output_dir: Directory to extract files into
        workers: Number of concurrent workers. Only affects Parquet vault unpacking. (optional)
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

        # Fix ownership and permissions on all extracted files
        # HMS runs as uid 1000, so we need to chown files to that user
        # This ensures full read/write access without overly permissive permissions
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


def make_sozip_of_dir(
    input_dir: str,
    output_file: str,
    chunk_size_kb: int = SOZIP_CHUNK_SIZE_KB,
    min_deflate_size_kb: int = SOZIP_MIN_DEFLATE_SIZE_KB,
    no_inspect_existing_compression: bool = False,
) -> bool:
    """Make a new SOZip (.zip) archive file from an existing directory. See CLI help for parameters."""
    err = False
    logger = setup_json_logging()
    logger.info(f"SOZipping: {input_dir} -> {output_file}")

    root = Path(input_dir)
    output = Path(output_file)

    # Validate args
    if not root.is_dir():
        logger.error(f"Not a directory: {input_dir}")
        err = True
    if output.suffix != ".zip":
        logger.error(f"Output must be a .zip file but got: {output.suffix}")
        err = True
    if output.exists():
        logger.error(f"Output already exists: {output_file}")
        err = True
    if err:
        return False

    # Validate contents of dir
    entries = sorted(root.rglob("*"))
    for path in entries:
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            logger.error(f"Unsupported entry: {path}")
            err = True
    if err:
        return False

    # Make the sozip
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output.parent) as tmp_dir:
            archive_file = Path(tmp_dir) / "model.zip"
            logger.debug(f"Writing to tmp archive: {archive_file}")
            with sozip.ZipFile(
                archive_file, "w", allowZip64=True, chunk_size=chunk_size_kb * 1024
            ) as archive:
                for path in entries:
                    method = (
                        zipfile.ZIP_STORED
                        if path.is_dir()
                        else recommended_compression_method(
                            path,
                            logger,
                            min_deflate_size_kb=min_deflate_size_kb,
                            no_inspect_existing_compression=no_inspect_existing_compression,
                        )
                    )
                    archive.write(
                        path, path.relative_to(root).as_posix(), compress_type=method
                    )
            logger.debug(f"Moving tmp archive: {archive_file} -> {output}")
            os.replace(archive_file, output)
    except (ImportError, OSError, RuntimeError, ValueError) as e:
        logger.error(f"Failed to SOZip: {input_dir} -> {output_file}. Error: {e}")
        return False

    logger.info(f"SOZipped: {input_dir} -> {output_file}")
    return True


def vault_to_sozip(
    input_file: str,
    output_file: str,
    chunk_size_kb: int = SOZIP_CHUNK_SIZE_KB,
    min_deflate_size_kb: int = SOZIP_MIN_DEFLATE_SIZE_KB,
    no_inspect_existing_compression: bool = False,
) -> bool:
    """Convert an existing model vault (.parquet) or .zip into a new SOZip file by extracting, then repacking. See CLI help for parameters."""
    logger = setup_json_logging()
    input_path = Path(input_file)
    output = Path(output_file)

    err = False
    if (
        not input_path.is_file()
        or input_path.suffix not in (".parquet", ".zip")
        or output.suffix != ".zip"
    ):
        logger.error(
            f"Conversion requires a .parquet or .zip input file, and a .zip output, but got {input_file!r} and {output_file!r}"
        )
        err = True
    if input_path.suffix.lower() == ".zip" and not zipfile.is_zipfile(input_path):
        logger.error(f"Invalid input .zip: {input_file}")
        err = True
    if output.exists():
        logger.error(f"Output already exists: {output_file}")
        err = True
    if err:
        return False

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output.parent) as tmp_dir:
            model_dir = Path(tmp_dir) / "model"
            if not unpack_local_file(input_file, str(model_dir)):
                return False
            if not make_sozip_of_dir(
                str(model_dir),
                output_file,
                chunk_size_kb=chunk_size_kb,
                min_deflate_size_kb=min_deflate_size_kb,
                no_inspect_existing_compression=no_inspect_existing_compression,
            ):
                return False
    except (ImportError, OSError, RuntimeError, ValueError) as e:
        logger.error(f"Failed to convert {input_file} -> {output_file}: {e}")
        return False

    logger.info(f"Converted: {input_file} -> {output_file}")
    return True


def extract_zip(zip_file: str, output_dir: str, logger) -> bool:
    """Extract a .zip after validating that no paths escape the output directory."""
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


def main():
    """Main entry point for vault operations."""
    parser = argparse.ArgumentParser(
        description="Vault pack/unpack utility for model archives",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
        Examples:
        # Pack a model directory to a new SOZip .zip, or re-pack an existing Parquet vault or .zip to a new SOZip .zip.
        vault.py pack /path/to/model /output/model.zip
        vault.py pack /path/to/model.parquet /output/model.zip
        vault.py pack /path/to/model.zip /output/model.zip

        # Unpack a local file
        vault.py unpack /path/to/model.parquet /output/dir

        # Fetch from S3 and unpack
        vault.py fetch-and-unpack s3://bucket/path/to/model.parquet /output/dir

        # With custom worker count
        vault.py unpack /path/to/model.parquet /output/dir --workers 16
                """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    pack_parser = subparsers.add_parser(
        "pack",
        allow_abbrev=False,
        help="Pack a model directory to a new SOZip .zip, or re-pack an existing Parquet vault or .zip to a new SOZip .zip.",
    )
    pack_parser.add_argument(
        "input_path",
        help="Existing model directory, .parquet vault file, or .zip file to be SOZipped.",
    )
    pack_parser.add_argument(
        "output_file", help="New .zip output path (must not yet exist)."
    )
    pack_parser.add_argument(
        "-c",
        "--chunk-size-kb",
        type=int,
        default=SOZIP_CHUNK_SIZE_KB,
        help=f"""SOZip chunk size in KB. Default: {SOZIP_CHUNK_SIZE_KB}.
Note that GDAL's sozip utility uses 64 KB as the chunk size,
but for cloud storage, >= 256 KB is recommended to reduce the number of requests.
""",
    )
    pack_parser.add_argument(
        "-s",
        "--min-deflate-size-kb",
        type=int,
        default=SOZIP_MIN_DEFLATE_SIZE_KB,
        help=f"""Kilobytes. Default: {SOZIP_MIN_DEFLATE_SIZE_KB}.
For each file, if smaller than this, use ZIP_STORED (do not compress, do not chunk) instead of ZIP_DEFLATED.
Provide 0 to ignore file size.
Note that GDAL's sozip utility uses 1024 KB (1 MB) as the minimum deflate size,
but a HEC-HMS model may contain a significant number of files in the 100 KB range.""",
    )
    pack_parser.add_argument(
        "-e",
        "--no-inspect-exist-compress",
        dest="no_inspect_existing_compression",
        action="store_true",
        help="""For each file, do not inspect existing compression.
Instead, choose compression based on file size only.
Overrides the default behavior which applies ZIP_STORED (do not compress, do not chunk)
for some extensions (e.g. .tif, .zip) after inspecting them to determine if they are already compressed.""",
    )

    # Unpack subcommand
    unpack_parser = subparsers.add_parser(
        "unpack", help="Unpack a local Parquet vault file or .zip file"
    )
    unpack_parser.add_argument(
        "input_file", help="Path to the local Parquet vault file or .zip file"
    )
    unpack_parser.add_argument("output_dir", help="Directory to extract files into")
    unpack_parser.add_argument(
        "--workers",
        type=int,
        help="Number of concurrent file writers. Only affects Parquet vault unpacking. (optional)",
    )

    # Fetch-and-unpack subcommand
    fetch_parser = subparsers.add_parser(
        "fetch-and-unpack", help="Fetch from S3 and unpack"
    )
    fetch_parser.add_argument(
        "s3_uri",
        help="S3 URI (e.g., s3://bucket/path/to/file.parquet or s3://bucket/path/to/file.zip)",
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
    if args.command == "pack":
        if args.chunk_size_kb <= 0:
            pack_parser.error("--chunk-size-kb must be positive")
        if args.min_deflate_size_kb < 0:
            pack_parser.error("--min-deflate-size-kb must be non-negative")
        pack_options = {
            "chunk_size_kb": args.chunk_size_kb,
            "min_deflate_size_kb": args.min_deflate_size_kb,
            "no_inspect_existing_compression": args.no_inspect_existing_compression,
        }
        input_path = Path(args.input_path)
        if input_path.is_dir():
            success = make_sozip_of_dir(
                args.input_path, args.output_file, **pack_options
            )
        elif input_path.is_file() and input_path.suffix.lower() in (".parquet", ".zip"):
            success = vault_to_sozip(args.input_path, args.output_file, **pack_options)
        else:
            pack_parser.error(
                f"input_path must be an existing model directory, .parquet vault file, or .zip file. Provided: {args.input_path}"
            )
    elif args.command == "unpack":
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
