FROM ghcr.io/dewberry/modelvault:main-a72c138 AS builder

FROM python:3.12-slim

LABEL org.opencontainers.image.title="Model Vault" \
    org.opencontainers.image.description="Unpack Parquet-based or zip-based model archives from local or S3 storage, or pack/repack a model into a SOZip .zip" \
    org.opencontainers.image.version="0.1.0" \
    org.opencontainers.image.vendor="Dewberry" \
    org.opencontainers.image.source="https://github.com/Dewberry/hmsbox" \
    org.opencontainers.image.documentation="https://github.com/Dewberry/hmsbox/tree/main/hms/vault"

# Install system dependencies
WORKDIR /app

# Git needed for installing Python packages from GitHub at commits to avoid tag mutability.
# sozipfile 568e15e53f65fb934f5d7723c50ee1ee1e5f94fa is v0.3.2: https://github.com/sozip/sozipfile/tree/v0.3.2
# tifftools 3e1a038a40ec05edba9f14d10b1aed81917511b9 is v1.7.0: https://github.com/DigitalSlideArchive/tifftools/tree/v1.7.0
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git \
    && pip install --no-cache-dir boto3 \
    "sozipfile @ git+https://github.com/sozip/sozipfile.git@568e15e53f65fb934f5d7723c50ee1ee1e5f94fa" \
    "tifftools @ git+https://github.com/DigitalSlideArchive/tifftools.git@3e1a038a40ec05edba9f14d10b1aed81917511b9" \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

# Copy modelvault binary from builder
COPY --from=builder /app/modelvault /usr/local/bin/modelvault

# Copy Python source code
COPY src/ src/
RUN chmod +x src/vault.py

# Set entrypoint to vault.py
ENTRYPOINT ["python3", "src/vault.py"]
