#!/bin/bash

# HMS_VERSION=${1:-4.12}
# HMS_VERSION=${1:-4.13}
# HMS_VERSION=${1:-4.13-beta.6}
HMS_VERSION=${1:-4.14-beta.1}

IMAGE=hmsbox-headless:$HMS_VERSION

docker build -t $IMAGE --build-arg HMS_VERSION=$HMS_VERSION ./headless --no-cache

LOCAL_MODEL_DIR=./model
HMS_MODEL_NAME=Trinity_Forecast.hms

CONTAINER_MODEL_DIR=/mnt/model

docker run --rm \
    -v $LOCAL_MODEL_DIR:$CONTAINER_MODEL_DIR \
    $IMAGE \
    $CONTAINER_MODEL_DIR/$HMS_MODEL_NAME \
    'Lookback'