#!/bin/bash

set -euo pipefail

HMS_VERSION="${HMS_VERSION:-4.14-beta.1}"
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


docker build -t "$IMAGE" --build-arg HMS_VERSION="$HMS_VERSION" ./forecast #--no-cache

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
