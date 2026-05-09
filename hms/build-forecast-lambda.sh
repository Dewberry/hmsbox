#!/bin/bash
set -e

# Build HMS Forecast Lambda container
# This creates a Lambda-compatible image for AWS Lambda deployment
#
# Usage:
#   ./build-forecast-lambda.sh [HMS_VERSION] [S3_BUCKET]
#
# Arguments:
#   HMS_VERSION - HEC-HMS version (default: 4.14-beta.1)
#   S3_BUCKET   - S3 bucket name to bake into image (optional)
#                 If not provided, HMS_S3_BUCKET env var required at runtime
#
# Examples:
#   ./build-forecast-lambda.sh 4.14-beta.1
#   ./build-forecast-lambda.sh 4.14-beta.1 my-prod-bucket

HMS_VERSION="${1:-4.14-beta.1}"
S3_BUCKET="${2:-}"

echo "Building HMS Forecast Lambda container with HMS version ${HMS_VERSION}..."
if [ -n "$S3_BUCKET" ]; then
    echo "S3 bucket will be baked into image: ${S3_BUCKET}"
else
    echo "No S3 bucket specified - HMS_S3_BUCKET environment variable will be required at runtime"
fi

# Check that required base images exist
echo "Checking for required base images..."
if ! docker image inspect hmsbox-headless:${HMS_VERSION} &> /dev/null; then
    echo "ERROR: hmsbox-headless:${HMS_VERSION} not found. Build it first with ./build-headless.sh"
    exit 1
fi

if ! docker image inspect hmsbox-converter:latest &> /dev/null; then
    echo "ERROR: hmsbox-converter:latest not found. Build it first with ./build-converter.sh"
    exit 1
fi

version=v0.1.6

# Build docker command with optional S3_BUCKET arg
BUILD_ARGS="--build-arg HMS_VERSION=${HMS_VERSION} --build-arg BUILD_VERSION=${version}"
if [ -n "$S3_BUCKET" ]; then
    BUILD_ARGS="${BUILD_ARGS} --build-arg S3_BUCKET=${S3_BUCKET}"
fi

# Build the Lambda image
docker build \
    --platform linux/amd64 \
    --provenance=false \
    --sbom=false \
    ${BUILD_ARGS} \
    -f forecast/Dockerfile.lambda \
    -t <accountid>.dkr.ecr.us-east-1.amazonaws.com/hmsbox-forecast:lambda-${HMS_VERSION}-${version} \
    -t <accountid>.dkr.ecr.us-east-1.amazonaws.com/hmsbox-forecast:lambda-latest \
    forecast/

echo ""
echo "Build complete!"
if [ -n "$S3_BUCKET" ]; then
    echo "Image configured for S3 bucket: ${S3_BUCKET}"
else
    echo "Image requires HMS_S3_BUCKET environment variable at runtime"
fi

docker push <accountid>.dkr.ecr.us-east-1.amazonaws.com/hmsbox-forecast:lambda-${HMS_VERSION}-${version}
docker push <accountid>.dkr.ecr.us-east-1.amazonaws.com/hmsbox-forecast:lambda-latest