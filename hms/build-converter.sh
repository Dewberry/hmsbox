#!/bin/bash

IMAGE=hmsbox-converter:latest

docker build -t $IMAGE ./converter #--no-cache

# HOST_DATA_DIR=./model/observations
HOST_DATA_DIR=/home/ubuntu/pilot/hmsbox/data/converter-test-data
DSS_DATA_INPUT_FILE=gages.dss
PARQUET_DATA_INPUT_FILE=gages.parquet

CONTAINER_DATA_DIR=/mnt/data

# docker run --rm \
#     -v $HOST_DATA_DIR:$CONTAINER_DATA_DIR \
#     $IMAGE 'dss-to-parquet' \
#     $CONTAINER_DATA_DIR/$DSS_DATA_INPUT_FILE \
#     -o $CONTAINER_DATA_DIR

docker run --rm \
    -v $HOST_DATA_DIR:$CONTAINER_DATA_DIR \
    $IMAGE 'parquet-to-dss' \
    $CONTAINER_DATA_DIR/$PARQUET_DATA_INPUT_FILE \
    -o $CONTAINER_DATA_DIR
