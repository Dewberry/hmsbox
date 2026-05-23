#!/bin/bash


IMAGE=hmsbox-notebook
VERSION=v0.1.20

ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
IMAGE_NAME=${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/${IMAGE}:${VERSION}

docker build -t $IMAGE_NAME .
docker run --rm  $IMAGE
# docker push $IMAGE_NAME