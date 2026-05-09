FROM ghcr.io/dewberry/modelvault:main-a72c138 AS builder

FROM python:3.12-slim

LABEL org.opencontainers.image.title="Model Vault" \
      org.opencontainers.image.description="Unpack Parquet-based model archives from local or S3 storage" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.vendor="Dewberry" \
      org.opencontainers.image.source="https://github.com/Dewberry/hmsbox" \
      org.opencontainers.image.documentation="https://github.com/Dewberry/hmsbox/tree/main/hms/vault"

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy modelvault binary from builder
COPY --from=builder /app/modelvault /usr/local/bin/modelvault

WORKDIR /app

# Copy Python requirements and install dependencies
RUN pip install --no-cache-dir boto3

# Copy Python source code
COPY src/ src/
RUN chmod +x src/vault.py

# Set entrypoint to vault.py
ENTRYPOINT ["python3", "src/vault.py"]
