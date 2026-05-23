#!/bin/bash

HMS_S3_BUCKET=flood-warning
IMAGE=hmsbox-notebook
VERSION=v0.1.1

ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
IMAGE_NAME=${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/${IMAGE}:${VERSION}

docker build -t $IMAGE_NAME .
# docker run --rm   \
# -e HMS_S3_BUCKET=$HMS_S3_BUCKET \
# $IMAGE_NAME


docker push $IMAGE_NAME