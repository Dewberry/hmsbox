# hmsbox-converter

Convert HEC-DSS files to/from Parquet / NetCDF (coming soon) format for efficient data processing and storage.

## Features

- **DSS to Parquet**: Extract time series data from HEC-DSS files into columnar Parquet format
- **Parquet to DSS**: Convert Parquet data back to HEC-DSS format
- **Iceberg Schema Support**: Default export uses semantic column names (timestamp, provider, site_id, variable, qualifier)
- **DSS Schema Support**: Optional generic DSS column names (datetime, A, B, C, F) with `--no-iceberg-schema`
- **Smart D Part Handling**: D part (date range) excluded from exports and auto-generated during imports
- **Smart E Part Handling**: E part (increment) excluded from exports by default, auto-detected from timestamps during imports
- **Structured JSON logging**: All logs in machine-readable JSON format (quiet by default)
- **Configurable grouping**: Group data by DSS path parts (default: F part)
- **Parallel processing**: Multi-threaded conversion for large datasets

## Quick Start

### Show Help
```bash
docker run hmsbox-converter:latest --help
```

### Show Version
```bash
docker run hmsbox-converter:latest --version
```

## Usage

### DSS to Parquet Conversion

Convert a DSS file to Parquet format:

```bash
docker run -v $(pwd):/data hmsbox-converter:latest \
  dss-to-parquet /data/input.dss -o /data/output.parquet
```

### Parquet to DSS Conversion

Convert Parquet back to DSS format:

```bash
docker run -v $(pwd):/data hmsbox-converter:latest \
  parquet-to-dss /data/input.parquet -o /data/output.dss
```

## Command Reference

### dss-to-parquet

Convert DSS file to Parquet format.

**Arguments:**
- `input_dss` - Path to input DSS file (required)
- `-o, --output` - Output Parquet file path (default: `input_name.parquet`)
- `--groupby` - DSS path part to group by (default: `F`)
- `--no-strip-suffix` - Do not strip version suffix from group keys
- `--include-parts` - Additional DSS path parts to include beyond A, B, C (default: `F`; E is auto-detected on import)
- `--no-iceberg-schema` - Use generic DSS column names (A, B, C, etc.) instead of semantic names
- `--group-workers` - Number of workers for group export (default: `4`)
- `--dss-workers` - Number of workers for DSS file processing (default: `1`)
- `--event-id` - Optional event ID to include in output
- `--sim-name` - Optional simulation name to include in output
- `-v, --verbose` - Show verbose DSS library output (default: suppressed)
- `-d, --debug` - Enable debug logging

**Schema Formats:**

*Iceberg Schema (default):*
- Semantic column names: `timestamp`, `value`, `provider`, `site_id`, `variable`, `qualifier`
- Maps DSS parts: A→provider, B→site_id, C→variable, F→qualifier
- D part (date range) excluded - auto-generated during import
- E part (increment) excluded by default - auto-detected from timestamps during import

*DSS Schema (with `--no-iceberg-schema`):*
- Generic column names: `datetime`, `value`, `A`, `B`, `C`, `F`
- Direct DSS path part names
- D part excluded - auto-generated during import
- E part excluded by default - auto-detected from timestamps during import

**Example (Iceberg Schema - default):**
```bash
docker run -v $(pwd):/data hmsbox-converter:latest \
  dss-to-parquet /data/model_output.dss \
  -o /data/output.parquet
```

**Example (DSS Schema):**
```bash
docker run -v $(pwd):/data hmsbox-converter:latest \
  dss-to-parquet /data/model_output.dss \
  --no-iceberg-schema \
  -o /data/output.parquet
```

### parquet-to-dss

Convert Parquet file to DSS format. Automatically handles both Iceberg and DSS schema formats.

**Arguments:**
- `input_parquet` - Path to input Parquet file (required)
- `-o, --output` - Output DSS file path (default: `input_basename.dss`)
- `--f-part` - Optional F part for DSS path
- `-v, --verbose` - Show verbose output (default: suppressed)
- `-d, --debug` - Enable debug logging

**Schema Detection:**
The converter automatically detects whether the input Parquet uses Iceberg schema (semantic names) or DSS schema (generic names) and converts accordingly.

**D Part Auto-Generation:**
The D part (date range) is automatically calculated from the time series data during import. For example, if your data spans February 1-28, 2026, the D part will be set to `01Feb2026-28Feb2026`.

**Example:**
```bash
docker run -v $(pwd):/data hmsbox-converter:latest \
  parquet-to-dss /data/results.parquet \
  -o /data/output.dss
```

## Important Notes

### D Part Handling

The D part of DSS paths (date range) is intentionally **excluded** from Parquet exports and **auto-generated** during imports:

- **Why excluded?** The D part is redundant - it's a text representation of the date range that's already encoded in the time series timestamps
- **Export behavior:** D column is not included in Parquet files (saves space, reduces redundancy)
- **Import behavior:** D part is automatically calculated from the actual time series date range
- **Round-trip safe:** Full fidelity is maintained - the reconstructed D part matches the original

**Example DSS Path Construction:**
```
Original:   /TRINITY RV/OAKWOOD TX/FLOW/01Feb2026-28Feb2026/15Minute/USGS/
Export:     timestamp, value, provider="TRINITY RV", site_id="OAKWOOD TX",
            variable="FLOW", qualifier="USGS"
            (no D or E columns by default)
Import:     /TRINITY RV/OAKWOOD TX/FLOW/01Feb2026-28Feb2026/15Minute/USGS/
            (D part auto-generated from timestamps, E part auto-detected from interval)
```

### E Part Handling

The E part of DSS paths (time interval) is by default **excluded** from Parquet exports and **auto-detected** during imports:

- **Why excluded?** The E part (e.g., "15Minute", "1Hour") can be reliably inferred from the timestamp spacing in the time series
- **Export behavior:** E column is not included in Parquet files by default (saves space, ensures consistency)
- **Import behavior:** E part is automatically detected by analyzing timestamp intervals for each unique provider/site_id/variable combination
- **Per-variable detection:** Each variable can have its own interval (e.g., 15-minute FLOW and 1-hour STAGE at same site)
- **Manual override:** Use `--include-parts E F` to explicitly include E in exports if needed

**Supported Intervals:**
The auto-detection supports 35 DSS time intervals from `1Year` down to `1Second`, including common intervals like `1Day`, `1Hour`, `15Minute`, etc.

## Output Format

Logs are structured JSON with the following fields:
- `ts` - ISO 8601 timestamp
- `level` - Log level (INFO, ERROR)
- `message` - Human-readable message

The top-level log schema is fixed to `ts`, `level`, and `message` for parser stability.
When extra details are available (for example manifests), they are encoded into the
`message` string.

### Verbose Mode

By default, DSS library output is suppressed for clean JSON logs. Use `-v` or `--verbose` to show all output:

```bash
docker run -v $(pwd):/data hmsbox-converter:latest \
  dss-to-parquet /data/input.dss --verbose
```

## Building

Build the Docker image:

```bash
cd hms/converter
docker build -t hmsbox-converter:latest .
```

Or use the build script from the parent directory:

```bash
cd hms
./build-converter.sh
```

## Volume Mounts

Mount your data directory to `/data` or any path in the container:

```bash
docker run -v /path/to/your/data:/data hmsbox-converter:latest dss-to-parquet /data/file.dss
```

## Exit Codes

- `0` - Success
- `1` - General error (file not found, conversion failed, etc.)
- `2` - Invalid arguments

## Requirements

- Docker or compatible container runtime
- HEC-DSS files (`.dss` format)
- Sufficient disk space for output files

## Dependencies

- Python 3.12
- hecdss (Python bindings for HEC-DSS)
- pandas & pyarrow (Parquet support)
- pydantic (data validation)
