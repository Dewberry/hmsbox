#!/bin/bash

IMAGE=hmsbox-vault:latest

docker build -t $IMAGE ./vault #--no-cache

HOST_DATA_DIR=./model
CONTAINER_DATA_DIR=/mnt/data
MODEL_VERSION=trinity-v20260509

YEAR=2026
MONTH=03
DAY=11
HOUR=01

# Forecast Model, Forcing, and Observations data
HMS_MODEL_VAULT=s3://flood-warning/dev/models/$MODEL_VERSION.parquet
FORCING_QPF=s3://flood-warning/staging/temporary/forcing/$YEAR/$MONTH/$DAY/$HOUR/hrrr_qpf.nc
FORCING_TEMP=s3://flood-warning/staging/temporary/forcing/$YEAR/$MONTH/$DAY/$HOUR/rtma_temp.nc
FORCING_QPE=s3://flood-warning/staging/temporary/forcing/$YEAR/$MONTH/$DAY/$HOUR/mrms_qpe.nc
OBSERVATIONS=s3://flood-warning/staging/temporary/observations/$YEAR/$MONTH/$DAY/$HOUR/gages.dss

echo "=== Building and Unpacking Model Vault with Forcing and Observations Data ==="

docker run --rm \
    -v $HOST_DATA_DIR:$CONTAINER_DATA_DIR \
    $IMAGE fetch-and-unpack $HMS_MODEL_VAULT $CONTAINER_DATA_DIR \
    --forcing $FORCING_QPF \
    --forcing $FORCING_TEMP \
    --forcing $FORCING_QPE \
    --observations $OBSERVATIONS

if [ $? -eq 0 ]; then
    echo "=== Complete ==="
    echo "Model files: $HOST_DATA_DIR/"
    echo "Forcing data: $HOST_DATA_DIR/forcing/"
    echo "Observations data: $HOST_DATA_DIR/observations/"
else
    echo "=== Failed ==="
    echo "Error during data extraction"
    exit 1
fi