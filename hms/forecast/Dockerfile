# Build arguments
ARG HMS_VERSION=4.14-beta.1
ARG S3_BUCKET

# Build stage for modelvault binary
FROM ghcr.io/dewberry/modelvault:main-a72c138 AS modelvault-builder

# Use the existing HMS container as base
FROM hmsbox-headless:${HMS_VERSION}

LABEL org.opencontainers.image.title="HEC-HMS Forecast Container" \
      org.opencontainers.image.description="HMS container with Python for DSS conversion and automated forecasting workflows" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.vendor="Dewberry" \
      org.opencontainers.image.source="https://github.com/Dewberry/hmsbox" \
      org.opencontainers.image.documentation="https://github.com/Dewberry/hmsbox/tree/main/hms/forecast"

USER root

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Copy modelvault binary for unpacking model archives
COPY --from=modelvault-builder /app/modelvault /usr/local/bin/modelvault

# Copy Python environment and source from pre-built DSS converter image
COPY --from=hmsbox-converter:latest /app /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy forecast-specific scripts and configuration
COPY src/ /usr/local/bin/
COPY config.yaml /app/config.yaml
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

# Substitute S3_BUCKET at build time if provided
ARG S3_BUCKET
RUN if [ -n "$S3_BUCKET" ]; then \
      echo "Substituting {S3_BUCKET} with: $S3_BUCKET"; \
      sed -i "s|{S3_BUCKET}|${S3_BUCKET}|g" /app/config.yaml; \
    else \
      echo "No S3_BUCKET build arg provided, will require HMS_S3_BUCKET at runtime"; \
    fi

RUN chmod +x /usr/local/bin/entrypoint.sh \
    /usr/local/bin/run.py \
    /usr/local/bin/parse_results_stats.py \
    /usr/local/bin/download_data.py \
    /usr/local/bin/upload_results.py

WORKDIR /app

# Install Python dependencies using uv
RUN rm -rf /app/.venv && \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python uv sync --frozen --no-dev

# Ensure venv is accessible to hms user
RUN chown -R hms:hms /app /opt/uv-python && \
    chmod -R a+rX /app/.venv /opt/uv-python

# Note: We run as root to allow writing to mounted directories
# The entrypoint will handle permissions for files created

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["--help"]
