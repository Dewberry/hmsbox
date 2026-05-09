# Model Vault Container

Docker container for unpacking model archives from local or S3 storage using [modelvault](https://github.com/Dewberry/modelvault).

## Overview

This container provides a simple interface to unpack Parquet-based model archives into their original file structures. Two operations are supported:

1. **`unpack`**: Extract a local Parquet archive
2. **`fetch-and-unpack`**: Download a Parquet archive from S3 and extract it. Optionally download additional forcing and observations data files from S3 to subdirectories alongside the model files.

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

### `unpack` - Extract Local Archive

Unpack a local Parquet file to a directory.

**Usage:**
```bash
docker run [OPTIONS] hmsbox-vault:latest unpack INPUT_FILE OUTPUT_DIR [--workers N]
```

**Arguments:**
- `INPUT_FILE`: Path to the Parquet archive file
- `OUTPUT_DIR`: Directory where files will be extracted
- `--workers N` (optional): Number of concurrent file writers (default: auto-detected CPU count)

**Example:**
```bash
docker run -v /data:/mnt/data hmsbox-vault:latest unpack /mnt/data/model.parquet /mnt/data/extracted
```

### `fetch-and-unpack` - Download and Extract from S3

Download a Parquet archive from S3 and extract it in one operation. Optionally download additional forcing and observations data files.

**Usage:**
```bash
docker run [OPTIONS] hmsbox-vault:latest fetch-and-unpack S3_URI [OUTPUT_DIR] [--workers N] [--forcing URI] [--observations URI]
```

**Arguments:**
- `S3_URI`: S3 URI in format `s3://bucket/path/to/file.parquet`
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