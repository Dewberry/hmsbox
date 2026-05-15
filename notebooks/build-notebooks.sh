#!/bin/bash

IMAGE=hmsbox-notebooks:latest

HOST_MODEL_DIR=../hms/model
HOST_DATA_DIR=../data
HOST_NOTEBOOKS_DIR=$(pwd)

# Build the image
docker build -t $IMAGE .

# Run notebook execution and export to HTML
docker run --rm \
    -v $HOST_MODEL_DIR:/notebooks/model \
    -v $HOST_DATA_DIR:/notebooks/data \
    -v $HOST_NOTEBOOKS_DIR:/notebooks/work \
    $IMAGE \
    bash -c "cd /notebooks/work && bash run-notebook.sh"

echo "HTML report: HMS_Analysis_Report.html"
