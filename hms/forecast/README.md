# hmsbox-forecast

Automated forecasting container that orchestrates HEC-HMS simulations with Python DSS conversion, S3 data management, and lookback-forecast workflows.

## Overview

This container extends the HMS headless container with integrated Python capabilities for:
- **Automated Forecasting Workflow**: Run lookback calibration followed by forecast simulation (default mode)
- **Data Management**: Download model vaults, forcing data, and observations from S3
- **Results Processing**: Convert DSS results to Parquet format
- **Results Upload**: Automatically upload processed results back to S3
- **Flexible Modes**: Predefined test/validation scenarios or real-time current datetime

## Quick Start

### Show Help
```bash
docker run hmsbox-forecast:latest
# or explicitly
docker run hmsbox-forecast:latest --help
```

### Run Full Automated Forecast (Default Workflow)

Download model and data, run lookback+forecast, export results, upload to S3:

```bash
docker run --rm \
    -e AWS_ACCESS_KEY_ID=<key> \
    -e AWS_SECRET_ACCESS_KEY=<secret> \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download --upload /mnt/model/Trinity_Forecast.hms
```

### Run with Test Mode

Use predefined test datetime for reproducible results:

```bash
docker run --rm \
    -v ~/.aws:/root/.aws:ro \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download --mode test /mnt/model/Trinity_Forecast.hms
```

## Workflow Modes

### Lookback-Forecast Mode (Default)

The default workflow runs two sequential simulations:

1. **Lookback Simulation**: Historical calibration run
   - Runs with `Lookback.control` file
   - Exports statistics to `results/stats.parquet`

2. **Forecast Simulation**: Forward prediction run
   - Runs with `Forecast.control` file
   - Exports full results to `results/forecast.parquet`

**Upload Behavior**: Results automatically uploaded to S3 (unless `--no-upload` specified)

**Example:**
```bash
docker run --rm \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download /mnt/model/Trinity_Forecast.hms
```

### Single-Run Mode (Legacy)

For running a single simulation with optional Python processing:

```bash
docker run --rm \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --single-run /mnt/model/Trinity_Forecast.hms Lookback \
    --python-args dss-to-parquet /mnt/model/Forecast.dss -o output.parquet
```

**Upload Behavior**: Results NOT uploaded by default (use `--upload` to enable)

## Configuration

The container uses `config.yaml` to define S3 paths, local directories, and predefined datetime modes:

**Key Configuration Sections:**

1. **Model Version**: Specifies which model archive to use
2. **S3 Path Templates**: Define locations for model vault, forcing data, observations, and results
3. **Local Paths**: Where files are stored in the container
4. **Datetime Modes**: Predefined scenarios for testing and validation

**Built-in Modes:**
- `test` - March 11, 2026 (for development testing)
- `validation1` - August 21, 2022 (Short duration, high intensity storms)
- `validation2` - June 15, 2023 (Medium duration, moderate intensity)
- `validation3` - May 28, 2024 (Long duration event with reservoir releases)

**Example Configuration:**
```yaml
model_version: "trinity-v20260509"

s3_paths:
  model_vault: "s3://flood-warning/dev/models/{model_version}.parquet"
  forcing:
    qpf: "s3://flood-warning/staging/temporary/forcing/{year}/{month:02d}/{day:02d}/{hour:02d}/hrrr_qpf.nc"
    temp: "s3://flood-warning/staging/temporary/forcing/{year}/{month:02d}/{day:02d}/{hour:02d}/rtma_temp.nc"
    qpe: "s3://flood-warning/staging/temporary/forcing/{year}/{month:02d}/{day:02d}/{hour:02d}/mrms_qpe.nc"
  observations: "s3://flood-warning/staging/temporary/observations/{year}/{month:02d}/{day:02d}/{hour:02d}/gages.dss"

local_paths:
  model_dir: "/mnt/model"
  forcing_dir: "/mnt/model/forcing"
  observations_dir: "/mnt/model/observations"

modes:
  test:
    year: 2026
    month: 3
    day: 11
    hour: 1
```

**Custom Configuration:**

Mount your own config file to override defaults:

```bash
docker run --rm \
    -v $(pwd)/custom-config.yaml:/app/config.yaml:ro \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download --mode test /mnt/model/Trinity_Forecast.hms
```

## Command-Line Options

### Data Management Options

**Download Control:**
- `--download` - Download model and data from S3 before running (default when no args)
- `--no-download` - Skip S3 downloads, use existing local files
- `--mode MODE` - Use predefined datetime mode: `test`, `validation1`, `validation2`, `validation3`
  - If not specified with `--download`, uses current UTC time
- `--skip-model` - Skip model vault download, only download forcing/observations (requires `--download`)

**Upload Control:**
- `--upload` - Upload results to S3 after completion
- `--no-upload` - Don't upload results to S3
- **Default behavior**: Upload enabled for lookback-forecast mode, disabled for single-run mode

### Workflow Control Options

**Mode Selection:**
- `--run-lookback-forecast` - Run lookback then forecast (default)
- `--single-run` - Run single simulation only (legacy mode, disables auto-upload)

**Control Files** (for lookback-forecast mode):
- `--lookback-control NAME` - Control file for lookback simulation (default: `Lookback`)
- `--forecast-control NAME` - Control file for forecast simulation (default: `Forecast`)

**Python Processing** (for lookback-forecast mode):
- `--lookback-python-args ARGS...` - Override default lookback post-processing
  - Default: `parse-results-stats {model_dir}/results/RUN_Lookback.results {model_dir}/results/stats.parquet`
- `--forecast-python-args ARGS...` - Override default forecast post-processing
  - Default: `dss-to-parquet {model_dir}/Forecast.dss -o {model_dir}/results/forecast.parquet`

### Execution Control Options (Single-Run Mode Only)

- `--hms-only` - Run only HMS simulation (skip Python converter)
- `--python-only` - Run only Python converter (skip HMS simulation)
- `--python-args ARGS...` - Arguments for Python converter in single-run mode

### Logging Options

- `--debug` - Enable full debug logging with raw output (includes DSS library verbosity)
- `--json-logs-only` - Show only JSON-formatted logs (default)
- `--no-json-logs-only` - Show all raw subprocess output

## Usage Examples

### Production Forecast (Current Time)

Run a real-time forecast using current UTC datetime:

```bash
docker run --rm \
    -e AWS_ACCESS_KEY_ID=<key> \
    -e AWS_SECRET_ACCESS_KEY=<secret> \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download /mnt/model/Trinity_Forecast.hms
```

This will:
1. Download model vault from S3 (using current timestamp)
2. Download forcing data (HRRR QPF, RTMA temp, MRMS QPE)
3. Download gage observations (gages.dss)
4. Run Lookback simulation with `Lookback.control`
5. Export lookback statistics to `results/stats.parquet`
6. Run Forecast simulation with `Forecast.control`
7. Export forecast results to `results/forecast.parquet`
8. Upload `stats.parquet` and `forecast.parquet` to S3

### Testing with Predefined Data

Use the test mode for reproducible results:

```bash
docker run --rm \
    -v ~/.aws:/root/.aws:ro \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download --mode test /mnt/model/Trinity_Forecast.hms
```

### Validation Scenarios

Run historical validation events:

```bash
# Validation 1: August 21, 2022 - High intensity storm event
docker run --rm \
    -v ~/.aws:/root/.aws:ro \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download --mode validation1 /mnt/model/Trinity_Forecast.hms

# Validation 2: June 15, 2023 - Moderate intensity event
docker run --rm \
    -v ~/.aws:/root/.aws:ro \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download --mode validation2 /mnt/model/Trinity_Forecast.hms
```

### Local Run (No Download/Upload)

Run with existing local model files:

```bash
docker run --rm \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --no-download --no-upload /mnt/model/Trinity_Forecast.hms
```

### Skip Model Download

If model is already unpacked, only download forcing and observations:

```bash
docker run --rm \
    -v ~/.aws:/root/.aws:ro \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download --mode test --skip-model /mnt/model/Trinity_Forecast.hms
```

### Custom Control Files

Use different control file names:

```bash
docker run --rm \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --lookback-control Historical --forecast-control Future \
    /mnt/model/Trinity_Forecast.hms
```

### Debug Mode

Enable verbose logging for troubleshooting:

```bash
docker run --rm \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --debug --download --mode test /mnt/model/Trinity_Forecast.hms
```

### Single Simulation (Legacy Mode)

Run just one simulation with custom Python processing:

```bash
docker run --rm \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --single-run /mnt/model/Trinity_Forecast.hms Lookback \
    --python-args dss-to-parquet /mnt/model/Forecast.dss -o /mnt/model/output.parquet
```

## Data Flow

### Download Process (when `--download` is specified)

1. **Load Configuration** - Read `/app/config.yaml`
2. **Determine Datetime** - Use specified `--mode` or current UTC time
3. **Construct S3 Paths** - Replace template variables with datetime values
4. **Download Model Vault** (unless `--skip-model`):
   - Downloads `.parquet` file from S3
   - Unpacks using `modelvault` binary
   - Extracts model files to `/mnt/model/`
5. **Download Forcing Data** to `/mnt/model/forcing/`:
   - `hrrr_qpf.nc` - HRRR Quantitative Precipitation Forecast
   - `rtma_temp.nc` - RTMA temperature analysis
   - `mrms_qpe.nc` - MRMS Quantitative Precipitation Estimate
6. **Download Observations** to `/mnt/model/observations/`:
   - `gages.dss` - Stream gage observations

### Lookback-Forecast Workflow

```
Download → Lookback HMS → Export Stats → Forecast HMS → Export Results → Upload
```

**Detailed Steps:**

1. Download model, forcing, and observations (if `--download`)
2. Run HMS with `Lookback.control`
3. Parse lookback results: `parse-results-stats RUN_Lookback.results stats.parquet`
4. Run HMS with `Forecast.control`
5. Convert forecast DSS to Parquet: `dss-to-parquet Forecast.dss forecast.parquet`
6. Upload results to S3 (if upload enabled)

### Upload Process (when enabled)

Uploads generated results back to S3:
- `results/stats.parquet` → S3 path from config with datetime
- `results/forecast.parquet` → S3 path from config with datetime

## Directory Structure

**After Full Workflow:**

```
/mnt/model/
├── Trinity_Forecast.hms       # HMS project file
├── Trinity_Forecast.control   # Project control
├── Trinity_Forecast.dss       # Project DSS database
├── Trinity_Forecast.gage      # Gage definitions
├── Trinity_Forecast.grid      # Grid definitions
├── Trinity_Forecast.terrain   # Terrain data
├── Lookback.control           # Lookback simulation control
├── Lookback.met               # Lookback meteorology
├── Forecast.control           # Forecast simulation control
├── Forecast.met               # Forecast meteorology
├── forcing/                   # Downloaded forcing data
│   ├── hrrr_qpf.nc
│   ├── rtma_temp.nc
│   └── mrms_qpe.nc
├── observations/              # Downloaded observations
│   └── gages.dss
└── results/                   # Generated results
    ├── RUN_Lookback.results   # Raw lookback results
    ├── stats.parquet          # Lookback statistics
    └── forecast.parquet       # Forecast output
```

## AWS Credentials

The container requires AWS credentials to access S3. Provide them via:

### Environment Variables
```bash
docker run --rm \
    -e AWS_ACCESS_KEY_ID=<your_key> \
    -e AWS_SECRET_ACCESS_KEY=<your_secret> \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download /mnt/model/Trinity_Forecast.hms
```

### AWS Credentials File
```bash
docker run --rm \
    -v ~/.aws:/root/.aws:ro \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download /mnt/model/Trinity_Forecast.hms
```

### IAM Role
When running on AWS infrastructure (EC2, ECS, Lambda), IAM roles are automatically used.

## Logging

The container outputs structured JSON logs for integration with logging systems:

```json
{"ts":"2026-05-09T14:32:10.123","level":"INFO","message":"HMS BOX    | forecast container initializing","run_download":true,"run_upload":true,"workflow":"lookback-forecast"}
{"ts":"2026-05-09T14:32:45.678","level":"INFO","message":"PROCESSING | export: stats from lookback"}
{"ts":"2026-05-09T14:33:12.901","level":"INFO","message":"PROCESSING | export: results from forecast"}
{"ts":"2026-05-09T14:33:45.234","level":"INFO","message":"HMS BOX    | Forecast container exited successfully"}
```

**Log Fields:**
- `ts` - ISO 8601 timestamp with millisecond precision
- `level` - Log level: `INFO`, `WARNING`, `ERROR`, `DEBUG`
- `message` - Human-readable message
- Additional context fields as appropriate

**Filtering Output:**
- Default: `--json-logs-only` shows only JSON logs from subprocesses
- Use `--no-json-logs-only` to see all raw output
- Use `--debug` for maximum verbosity (enables DEBUG level + raw output)

## Building

Build the forecast container:

```bash
cd hms
./build-forecast.sh
```

**Build Process:**
1. Uses `hmsbox-headless:4.14-beta.1` as base image (HMS simulation engine)
2. Copies `modelvault` binary from modelvault image (for unpacking model archives)
3. Copies Python DSS converter environment from `hmsbox-converter:latest`
4. Installs forecast-specific scripts: `run.py`, `download_data.py`, `upload_results.py`, `parse_results_stats.py`
5. Copies `config.yaml` configuration
6. Installs Python dependencies using `uv` (boto3, pyyaml, pandas, etc.)

**Build Requirements:**
- `hmsbox-headless:4.14-beta.1` must be built first
- `hmsbox-converter:latest` must be built first
- Internet access to pull `ghcr.io/dewberry/modelvault` and `ghcr.io/astral-sh/uv`

**Build with Download Testing:**
```bash
cd hms
./build-forecast-with-download.sh
```

This also runs a test download to verify S3 connectivity and config.

## Dependencies

**Base Images:**
- `hmsbox-headless:4.14-beta.1` - HEC-HMS simulation engine with Java runtime
- `hmsbox-converter:latest` - Python DSS conversion utilities with hecdss library
- `ghcr.io/dewberry/modelvault:main-a72c138` - Model archive packing/unpacking

**Python Dependencies:**
- `boto3` - AWS S3 client
- `pyyaml` - Configuration file parsing
- `pandas` - Data manipulation
- `pyarrow` - Parquet file support
- `hecdss` - DSS file reading/writing

**System Dependencies:**
- Python 3.12
- Java 17 (from headless base)
- HMS 4.14-beta.1 binaries (from headless base)

## Version

Current version: **0.1.0**

Compatible with:
- HEC-HMS 4.14-beta.1
- Python 3.12
- DSS 7.x format
