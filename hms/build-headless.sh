#!/bin/bash

# HMS_VERSION=${1:-4.12}
# HMS_VERSION=${1:-4.13}
# HMS_VERSION=${1:-4.13-beta.6}
HMS_VERSION=${1:-4.14-beta.1}

IMAGE=hmsbox-headless:$HMS_VERSION

# docker build -t $IMAGE --build-arg HMS_VERSION=$HMS_VERSION ./headless

# MODEL_VERSION=trinity-v20260509
MODEL_VERSION=trinity-v20260522.1

# HOST_MODEL_DIR=./model
HOST_MODEL_DIR=/home/ubuntu/pilot/hmsbox/$MODEL_VERSION
rm $HOST_MODEL_DIR/Lookback.dss
rm $HOST_MODEL_DIR/Lookback.out
rm $HOST_MODEL_DIR/Lookback.log
# rm $HOST_MODEL_DIR/Lookback.dss
rm $HOST_MODEL_DIR/Trinity_Forecast.out
rm $HOST_MODEL_DIR/Trinity_Forecast.log

HMS_MODEL_NAME=Trinity_Forecast.hms
SIMULATION_MODE=Lookback

CONTAINER_MODEL_DIR=/mnt/model

docker run --rm \
    -v $HOST_MODEL_DIR:$CONTAINER_MODEL_DIR \
    $IMAGE \
    $CONTAINER_MODEL_DIR/$HMS_MODEL_NAME \
    $SIMULATION_MODE