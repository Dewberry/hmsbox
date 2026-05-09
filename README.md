# hmsbox

Cloud-native containerized tools for automated HEC-HMS hydrological forecasting workflows.

## Project Status: Pre-Alpha / Bleeding Edge

**This project is in very early development and is not ready for production use.**

- Documentation is incomplete
- APIs and data models are subject to breaking changes
- Sample data and complete setup instructions are not yet available
- Active development and experimentation ongoing

## Overview

`hmsbox` is a suite of Docker containers designed to automate hydrological forecasting workflows using HEC-HMS in cloud-native environments. The toolkit enables operational flood forecasting by orchestrating model runs, data downloads from cloud storage, and results processing into modern data formats.

**Design Philosophy:**
- Start with a calibrated HMS model paired with observation data (gages / observations)
- Automate lookback-forecast workflows for real-time forecasting
- Use cloud-native data formats: Parquet for tabular data, NetCDF for gridded forcing
- Support S3-based data management for scalability
- Provide structured JSON logging for observability
- Enable headless execution suitable for containers, serverless, and CI/CD

**Target Use Case:**
Operational flood forecasting systems that need to:
1. Download real-time forcing data (precipitation, temperature) from S3
2. Download stream gage observations from S3
3. Run HMS lookback simulation for calibration
4. Run HMS forecast simulation for predictions
5. Export results to Parquet for analysis and visualization
6. Upload processed results back to S3

## Architecture

The toolkit consists of four Docker containers that build upon each other:

```
hmsbox-headless          # Base: HMS simulation engine
    ↓ (extends)
hmsbox-converter         # Adds: DSS ↔ Parquet conversion
    ↓ (used by)
hmsbox-forecast          # Adds: S3 data management, workflow orchestration

hmsbox-vault             # Standalone: Model archive management
```

### Container Relationships

**Inheritance Chain:**
1. **hmsbox-headless** provides the HMS simulation engine
2. **hmsbox-forecast** extends headless, copies Python environment from converter
3. **hmsbox-vault** is standalone but integrates with forecast for model downloads

**Dependencies:**
- `hmsbox-converter` must be built before `hmsbox-forecast`
- `hmsbox-headless` must be built before `hmsbox-forecast`
- `hmsbox-vault` can be built independently

## Containers

### 1. hmsbox-headless

**Purpose:** Run HEC-HMS hydrological simulations in headless mode (no GUI).

**Key Features:**
- Containerized HMS 4.14-beta.1 with Java 17 runtime
- Headless JavaFX configuration (no display required)
- Structured JSON logging
- Non-root user execution
- Multi-version support (4.12, 4.13, 4.14-beta.1)

**Use Case:** Run HMS simulations in Docker, Kubernetes, AWS Batch, or other container platforms.

**Documentation:** [hms/headless/README.md](hms/headless/README.md)

**Example:**
```bash
docker run -v $(pwd)/model:/mnt/model \
    hmsbox-headless:4.14-beta.1 \
    /mnt/model/Project.hms SimulationName
```

---

### 2. hmsbox-converter

**Purpose:** Convert HEC-DSS files to/from Parquet format for cloud-native workflows.

**Key Features:**
- DSS to Parquet conversion with configurable schemas
- Parquet to DSS conversion (roundtrip capable)
- Iceberg-compatible schema with semantic column names
- Smart handling of DSS path parts (auto-generate D part, detect E part)
- Parallel processing for large datasets
- Python 3.12 with `hecdss` library

**Use Case:** Extract HMS results from DSS format into Parquet for analysis, visualization, or storage in data lakes.

**Documentation:** [hms/converter/README.md](hms/converter/README.md)

**Example:**
```bash
docker run -v $(pwd)/data:/data hmsbox-converter:latest \
    dss-to-parquet /data/results.dss -o /data/results.parquet
```

---

### 3. hmsbox-forecast

**Purpose:** Automated forecasting workflow with S3 data management and lookback-forecast orchestration.

**Key Features:**
- **Lookback-Forecast Workflow**: Run calibration (lookback) then prediction (forecast) automatically
- **S3 Integration**: Download model vaults, forcing data (NetCDF), observations (DSS)
- **Results Processing**: Export lookback statistics and forecast results to Parquet
- **S3 Upload**: Push processed results back to S3
- **Configurable Modes**: Predefined test/validation scenarios or real-time current datetime
- **Combines**: HMS execution + DSS conversion + Python data management

**Use Case:** Production operational forecasting with automated data pipeline from S3 → HMS → Parquet → S3.

**Documentation:** [hms/forecast/README.md](hms/forecast/README.md)

**Example:**
```bash
docker run -v ~/.aws:/root/.aws:ro -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download --mode test /mnt/model/Trinity_Forecast.hms
```

This single command:
1. Downloads model archive from S3 and unpacks it
2. Downloads forcing data (HRRR QPF, RTMA temp, MRMS QPE)
3. Downloads gage observations
4. Runs lookback simulation and exports statistics
5. Runs forecast simulation and exports results
6. Uploads results to S3

---

### 4. hmsbox-vault

**Purpose:** Unpack Parquet-based model archives from S3 or local storage.

**Key Features:**
- Unpack compressed model archives in Parquet format
- S3 download and extraction in one operation
- Optional forcing and observations downloads
- Uses [modelvault](https://github.com/Dewberry/modelvault) for archive management

**Use Case:** Standalone model archive management, or integrated with forecast workflow.

**Documentation:** [hms/vault/README.md](hms/vault/README.md)

**Example:**
```bash
docker run -v ~/.aws:/root/.aws:ro -v $(pwd)/data:/mnt/data \
    hmsbox-vault:latest \
    fetch-and-unpack s3://bucket/models/model.parquet /mnt/data
```

## Data Model (In Development)

The `hmsbox` data model is **opinionated** and **incomplete**, focusing on elements most suitable for operational forecasting:

### Input Data
- **Model Archive**: Parquet-based compressed model files (via modelvault)
- **Forcing Data**: NetCDF format for gridded meteorological inputs
  - Precipitation forecasts (HRRR QPF)
  - Temperature analysis (RTMA)
  - Precipitation estimates (MRMS QPE)
- **Observations**: DSS format for stream gage data
  - Real-time gage readings
  - Historical observations for calibration

### Output Data
- **Lookback Statistics**: Parquet format with performance metrics
- **Forecast Results**: Parquet format with time series predictions
- **JSON Logs**: Structured logs for monitoring and debugging

### Storage Strategy
- **Cloud-native formats**: Parquet instead of DSS for outputs
- **S3 path templates**: Configurable datetime-based paths
- **Versioned models**: Model archives include version identifiers
- **Time-partitioned**: Results organized by forecast run datetime

**Note:** The data model is subject to change as we refine requirements for operational forecasting systems.

## Getting Started

### Prerequisites
- Docker installed
- AWS credentials (for S3 operations)
- Calibrated HEC-HMS model
- Access to forcing data and observation data sources

### Build Order

Build containers in the following order due to dependencies:

```bash
cd hms

# 1. Build headless (required for forecast)
./build-headless.sh

# 2. Build converter (required for forecast)
./build-converter.sh

# 3. Build vault (optional, standalone)
./build-vault.sh

# 4. Build forecast (requires headless + converter)
./build-forecast.sh
```

### Quick Test

After building, test with the forecast container:

```bash
# Show available options
docker run hmsbox-forecast:latest --help

# Run test mode (requires AWS credentials and S3 data)
docker run --rm \
    -v ~/.aws:/root/.aws:ro \
    -v $(pwd)/model:/mnt/model \
    hmsbox-forecast:latest \
    --download --mode test /mnt/model/Trinity_Forecast.hms
```

**Note:** This requires:
1. AWS credentials with S3 access
2. Properly configured S3 buckets with forcing/observation data
3. Model archive available in S3
4. Configuration file with correct S3 paths

Complete setup instructions will be provided as the project matures.

## Configuration

The forecast container uses `config.yaml` to define:
- S3 path templates for model vaults, forcing, observations, and results
- Local directory paths
- Predefined datetime modes for testing and validation

See [hms/forecast/config.yaml](hms/forecast/config.yaml) for the structure.

## Logging

All containers produce structured JSON logs for integration with logging systems:

```json
{"ts":"2026-05-09T14:32:10.123","level":"INFO","message":"HMS BOX | forecast container initializing"}
{"ts":"2026-05-09T14:33:45.234","level":"INFO","message":"HMS BOX | Forecast container exited successfully"}
```

## Use Cases

### Operational Flood Forecasting
1. Scheduled runs (cron, AWS EventBridge)
2. Download latest forcing data and observations
3. Run lookback for calibration
4. Run forecast for predictions
5. Upload results for visualization

### Model Validation
1. Use predefined validation modes
2. Run historical events with known outcomes
3. Compare forecast vs. observed results
4. Iterate on model calibration

### Development and Testing
1. Use test mode with fixed datetime
2. Reproducible results for debugging
3. Local runs without S3 dependencies

## Cloud Deployment

The containers are designed for cloud-native deployment:

- **AWS Lambda**: Serverless forecast execution (up to 15 min)
  - Event-driven or scheduled triggers
  - Supports images up to 10GB
  - Lambda-specific build available: `./build-forecast-lambda.sh`
  - See [hms/forecast/LAMBDA.md](hms/forecast/LAMBDA.md) for deployment guide
- **AWS Batch**: Best for long-running jobs (no 15-min limit)
  - Scheduled or event-driven forecast runs
  - Better for complex models that exceed Lambda timeout
- **Kubernetes**: Scalable forecast processing with CronJobs
  - Horizontal scaling for multiple concurrent forecasts
  - Native integration with cloud storage
- **ECS/Fargate**: Container-based always-on services
  - Long-running forecast services
  - Integration with load balancers and service discovery
- **GitHub Actions**: CI/CD for model testing and validation
  - Automated testing of model changes
  - Validation runs for new data sources

## Roadmap

- [ ] Stabilize data model and S3 path structures
- [ ] Complete documentation with end-to-end examples
- [ ] Provide sample datasets and models


## Contributing

This project is in early development. Contributions are welcome but expect frequent breaking changes.

Areas of focus:
- Testing with various HMS model configurations
- Data model refinement
- Cloud deployment patterns
- Performance optimization

## License

See [LICENSE](LICENSE) file for details.

## References

- [HEC-HMS](https://www.hec.usace.army.mil/software/hec-hms/) - Hydrologic Modeling System
- [modelvault](https://github.com/Dewberry/modelvault) - Model archive format
