#!/bin/bash

set -euo pipefail

# Build HMS Forecast container
#
# Usage:
#   ./build-forecast.sh [S3_BUCKET]
#
# Arguments:
#   S3_BUCKET   - S3 bucket name to bake into image (optional)
#                 If not provided, HMS_S3_BUCKET env var required at runtime
#
# Environment Variables:
#   HMS_VERSION - HEC-HMS version (default: 4.14-beta.1)
#
# Examples:
#   ./build-forecast.sh
#   ./build-forecast.sh my-prod-bucket
#   HMS_VERSION=4.14-beta.2 ./build-forecast.sh my-bucket

HMS_VERSION="${HMS_VERSION:-4.14-beta.1}"
S3_BUCKET="${1:-}"
IMAGE="hmsbox-forecast:${HMS_VERSION}"

HOST_MODEL_DIR=./model
CONTAINER_MODEL_DIR=/mnt/model

HMS_MODEL_NAME=Trinity_Forecast.hms

FORECAST_START="${FORECAST_START:-2026-03-11T01:00:00Z}"
RUNTIME_PARAMS="{\"forecast_start\":\"${FORECAST_START}\",\"control_type\":\"Lookback\"}"

FORECAST_DSS_PATH="${CONTAINER_MODEL_DIR}/Forecast.dss"
FORECAST_PQ_PATH="${CONTAINER_MODEL_DIR}/results/Forecast-${FORECAST_START:0:13}.parquet"

LOOKBACK_STATS_XML_PATH="${CONTAINER_MODEL_DIR}/results/RUN_Lookback.results"
LOOKBACK_STATS_PQ_PATH="${CONTAINER_MODEL_DIR}/results/Skill-${FORECAST_START:0:13}.parquet"

echo "Building HMS Forecast container with HMS version ${HMS_VERSION}..."
if [ -n "$S3_BUCKET" ]; then
    echo "S3 bucket will be baked into image: ${S3_BUCKET}"
    docker build -t "$IMAGE" --build-arg HMS_VERSION="$HMS_VERSION" --build-arg S3_BUCKET="$S3_BUCKET" ./forecast
else
    echo "No S3 bucket specified - HMS_S3_BUCKET environment variable will be required at runtime"
    docker build -t "$IMAGE" --build-arg HMS_VERSION="$HMS_VERSION" ./forecast
fi

echo ""
echo "Build complete!"
if [ -n "$S3_BUCKET" ]; then
    echo "Image configured for S3 bucket: ${S3_BUCKET}"
else
    echo "Image requires HMS_S3_BUCKET environment variable at runtime"
fi
echo ""

docker run --rm \
    -v $HOST_MODEL_DIR:$CONTAINER_MODEL_DIR \
    -e "params=${RUNTIME_PARAMS}" \
    $IMAGE \
    $CONTAINER_MODEL_DIR/$HMS_MODEL_NAME \
    "Lookback" \
    --python-args parse-results-stats $LOOKBACK_STATS_XML_PATH $LOOKBACK_STATS_PQ_PATH


docker run --rm \
    -v $HOST_MODEL_DIR:$CONTAINER_MODEL_DIR \
    -e "params=${RUNTIME_PARAMS}" \
    $IMAGE \
    $CONTAINER_MODEL_DIR/$HMS_MODEL_NAME \
    "Forecast" \
    --python-args dss-to-parquet $FORECAST_DSS_PATH -o $FORECAST_PQ_PATH
