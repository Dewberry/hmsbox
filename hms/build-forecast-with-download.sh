#!/bin/bash
# Build and run HMS forecast with automatic data download from S3

set -e  # Exit on error

IMAGE=hmsbox-forecast:4.14-beta.1
HMS_S3_BUCKET="${1:-flood-warning}"  # S3 bucket can be passed as an argument, defaults to 'flood-warning'

# Build the image (if not already built)
docker build -t $IMAGE ./forecast

HOST_MODEL_DIR=./model
CONTAINER_MODEL_DIR=/mnt/model

HMS_MODEL_NAME=Trinity_Forecast.hms

FORECAST_DSS_PATH="${CONTAINER_MODEL_DIR}/Forecast.dss"
LOOKBACK_DSS_PATH="${CONTAINER_MODEL_DIR}/Lookback.dss"

# Test 1: Download data using test mode (March 11, 2026)
# Runs Lookback simulation followed by Forecast simulation (default behavior)
# Download is enabled by default, so --download flag is optional
# Output paths are managed by the container with hardcoded defaults:
#   - Lookback stats: /mnt/model/results/stats.parquet
#   - Forecast output: /mnt/model/results/forecast.parquet

docker run --rm \
    -v $HOST_MODEL_DIR:$CONTAINER_MODEL_DIR \
    -e HMS_S3_BUCKET="$HMS_S3_BUCKET" \
    $IMAGE --mode test \
    $CONTAINER_MODEL_DIR/$HMS_MODEL_NAME

# Test 2: Download data using validation1 mode (August 21, 2022)
# docker run --rm \
#     -v $HOST_MODEL_DIR:$CONTAINER_MODEL_DIR \
#     $IMAGE --mode validation1 \
#     $CONTAINER_MODEL_DIR/$HMS_MODEL_NAME

# Test 3: Operation Mode: Download data using current date/time
# docker run --rm \
#     -v $HOST_MODEL_DIR:$CONTAINER_MODEL_DIR \
#     $IMAGE \
#     $CONTAINER_MODEL_DIR/$HMS_MODEL_NAME

# Test 4: Download only forcing/observations (skip model vault)
# docker run --rm \
#     -v $HOST_MODEL_DIR:$CONTAINER_MODEL_DIR \
#     $IMAGE --mode test --skip-model \
#     $CONTAINER_MODEL_DIR/$HMS_MODEL_NAME

# Test 5: Run without downloading (assumes data is already in place)
# docker run --rm \
#     -v $HOST_MODEL_DIR:$CONTAINER_MODEL_DIR \
#     $IMAGE --no-download \
#     $CONTAINER_MODEL_DIR/$HMS_MODEL_NAME

if [ $? -eq 0 ]; then
    echo ""
    echo "=== Complete ==="

else
    echo ""
    echo "=== Failed ==="
    echo "Error during forecast execution"
    exit 1
fi