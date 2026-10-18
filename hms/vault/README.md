# Model Vault Container

Docker container for unpacking model archives from local or S3 storage using [modelvault](https://github.com/Dewberry/modelvault).

## Overview

This container provides a simple interface to unpack models stored as Parquet vault files or .zip archives, as well as pack or repack models into new SOZip .zip archives. Three operations are supported:

1. **`unpack`**: Extract a local Parquet vault file or .zip archive
2. **`fetch-and-unpack`**: Download a Parquet vault file or .zip archive from S3 and extract it. Optionally download additional forcing and observations data files from S3 to subdirectories alongside the model files.
3. **`pack`**: Pack an HMS model directory, or repack an existing Parquet vault file or .zip file, into a new [Seek-Optimized ZIP (SOZip)](https://sozip.org) (`.zip`) archive.

For details on the modelvault format, archive structure, and pack/unpack operations, see the [modelvault repository](https://github.com/Dewberry/modelvault).

## Quick Start

### Build the Container

```bash
docker build -t hmsbox-vault:latest ./vault
```

### Unpack a Local Archive

```bash
docker run \
  -v /local/path/to/models:/mnt/data \
  hmsbox-vault:latest unpack /mnt/data/model.parquet /mnt/data/output
```

### Fetch and Unpack from S3

```bash
docker run \
  -e AWS_ACCESS_KEY_ID=<your-key> \
  -e AWS_SECRET_ACCESS_KEY=<your-secret> \
  -v /local/path:/mnt/data \
  hmsbox-vault:latest fetch-and-unpack \
    s3://bucket/path/to/model.parquet /mnt/data \
    --forcing s3://bucket/forcing/data.nc \
    --observations s3://bucket/observations/gages.dss
```

This will extract:
- Model files to `/local/path/`
- Forcing data to `/local/path/forcing/`
- Observations data to `/local/path/observations/`

## Commands

### `pack` - Create a new SOZip .zip file from a model directory, Parquet vault file, or .zip

```bash
pack /mnt/data/model /mnt/data/model.zip
pack /mnt/data/model.parquet /mnt/data/model.zip
pack /mnt/data/model.zip /mnt/data/model-sozip.zip
```

The input must be either an existing model directory, or `.parquet` vault file, or valid `.zip` file.

Directories are packed directly. Archive files are extracted fully first, and then repacked.

The output must not already exist.

#### ZIP Creation Details

Symlinks and special files in source directories are rejected rather than followed.

By default, when adding some files to the archive (e.g. existing .tif and .zip files), they are inspected to determine if they are already compressed,
and if so they are stored as ZIP_STORED (do not compress, do not chunk) regardless of size. To disable this behavior (to use size only to choose compression and chunking), provide `--no-inspect-exist-compress`. Since .zip files can contain a mix of compressed and uncompressed members, the adequacy of the existing compressed is False if its uncompressed members total more than 4 MB or represent more than 20% of the total bytes.

- `--chunk-size-kb`: SOZip chunk size in KB. Default: 512 KB. Note that GDAL's sozip utility uses 64 KB as the chunk size, but for cloud storage, >= 256 KB is recommended to reduce the number of requests.
- `--min-deflate-size-kb`: Kilobytes. Default: 64 KB. For each file, if smaller than this, use ZIP_STORED (do not compress, do not chunk) instead of ZIP_DEFLATED. Provide 0 to ignore file size. Note that GDAL's sozip utility uses 1024 KB (1 MB) as the minimum deflate size, but a HEC-HMS model may contain a significant number of files in the 100 KB range.
- `--no-inspect-exist-compress`: For each file, do not inspect existing compression. Instead, choose compression based on file size only. Overrides the default behavior which applies ZIP_STORED (do not compress, do not chunk) for some extensions (e.g. .tif, .zip) after inspecting them to determine if they are already compressed.

Parquet input is restored with `modelvault` before repacking to SOZip. Original files structure is retained, but Parquet catalog (including model name/version, file hashes, and packing timestamps) are not retained.

### `unpack` - Extract Local Archive

Unpack a local Parquet or .zip file to a directory.

**Usage:**
```bash
docker run [OPTIONS] hmsbox-vault:latest unpack INPUT_FILE OUTPUT_DIR [--workers N]
```

**Arguments:**
- `INPUT_FILE`: Path to the Parquet or .zip archive file
- `OUTPUT_DIR`: Directory where files will be extracted
- `--workers N` (optional): Number of concurrent file writers (default: auto-detected CPU count)

**Example:**
```bash
docker run -v /data:/mnt/data hmsbox-vault:latest unpack /mnt/data/model.parquet /mnt/data/extracted
```

### `fetch-and-unpack` - Download and Extract from S3

Download a Parquet or .zip archive from S3 and extract it in one operation. Optionally download additional forcing and observations data files.

**Usage:**
```bash
docker run [OPTIONS] hmsbox-vault:latest fetch-and-unpack S3_URI [OUTPUT_DIR] [--workers N] [--forcing URI] [--observations URI]
```

**Arguments:**
- `S3_URI`: S3 URI in format `s3://bucket/path/to/file.parquet` or `s3://bucket/path/to/file.zip`
- `OUTPUT_DIR` (optional): Directory to extract files into (default: `/mnt/data/unpacked`)
- `--workers N` (optional): Number of concurrent file writers (default: auto-detected CPU count)
- `--forcing URI` (optional): S3 URI for forcing data file. Can be specified multiple times for multiple files
- `--observations URI` (optional): S3 URI for observations data file. Can be specified multiple times for multiple files

**Example - Basic:**
```bash
docker run \
  -e AWS_ACCESS_KEY_ID=<key> \
  -e AWS_SECRET_ACCESS_KEY=<secret> \
  -v /data:/mnt/data \
  hmsbox-vault:latest fetch-and-unpack s3://my-bucket/models/v1.parquet /mnt/data/models
```

**Example - With Forcing and Observations:**
```bash
docker run \
  -v /data:/mnt/data \
  hmsbox-vault:latest fetch-and-unpack \
    s3://bucket/models/model-v1.parquet /mnt/data \
    --forcing s3://bucket/forcing/hrrr_qpf.nc \
    --forcing s3://bucket/forcing/rtma_temp.nc \
    --forcing s3://bucket/forcing/mrms_qpe.nc \
    --observations s3://bucket/observations/gages.dss
```

**Note:**
- Model files are unpacked to `OUTPUT_DIR/`
- Forcing files are downloaded to `OUTPUT_DIR/forcing/`
- Observations files are downloaded to `OUTPUT_DIR/observations/`
- Example: If `OUTPUT_DIR` is `/mnt/data`, model files go to `/mnt/data/`, forcing to `/mnt/data/forcing/`, observations to `/mnt/data/observations/`

## Environment Variables

For S3 operations, configure AWS credentials:

- `AWS_ACCESS_KEY_ID`: AWS access key ID
- `AWS_SECRET_ACCESS_KEY`: AWS secret access key
- `AWS_DEFAULT_REGION`: AWS region (optional, default: us-east-1)

## Logging

All operations produce structured JSON logs with:
- `ts`: ISO 8601 timestamp with millisecond precision
- `level`: Log level (INFO, ERROR)
- `message`: Descriptive message

**Example:**
```json
{"ts":"2026-05-09T10:44:09.200","level":"INFO","message":"Successfully unpacked /mnt/data/model.parquet"}
```

## Build Script

Use `build-vault.sh` to build the image and test with a sample S3 model including forcing and observations data:

```bash
chmod +x build-vault.sh
./build-vault.sh
```

This script:
1. Builds the `hmsbox-vault:latest` image
2. Runs `fetch-and-unpack` to download and extract a model from S3
3. Downloads forcing data files (QPF, temperature, QPE) to `./data/forcing/`
4. Downloads observations data file to `./data/observations/`
5. Mounts local `./data` directory as container `/mnt/data`
6. Exits with error code 1 if any operation fails

The script can be customized by editing the variables at the top:
- `MODEL_VERSION`: Model version to download
- `YEAR`, `MONTH`, `DAY`, `HOUR`: Timestamp for forcing/observations data
- `HMS_MODEL_VAULT`, `FORCING_*`, `OBSERVATIONS`: S3 URIs for data files

## References

- [modelvault Repository](https://github.com/Dewberry/modelvault) - Core archive format and tool documentation
- [modelvault Archive Format](https://github.com/Dewberry/modelvault#output-structure) - Parquet schema details
- [modelvault Pack Command](https://github.com/Dewberry/modelvault#pack-command) - Create archives from model files