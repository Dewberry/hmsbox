# Declare HMS_VERSION globally before any FROM
ARG HMS_VERSION=4.14-beta.1

# Build stage for modelvault binary
FROM ghcr.io/dewberry/modelvault:main-a72c138 AS modelvault-builder

# Use the existing HMS container as base
FROM hmsbox-headless:${HMS_VERSION}

LABEL org.opencontainers.image.title="HEC-HMS Forecast Container" \
      org.opencontainers.image.description="HMS container with Python for DSS conversion"

USER root

# Install Python 3
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Copy modelvault binary for unpacking model archives
COPY --from=modelvault-builder /app/modelvault /usr/local/bin/modelvault

# Copy Python environment and source from pre-built DSS image
COPY --from=hmsbox-converter:latest /app /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy forecast-specific scripts and configuration
COPY src/ /usr/local/bin/
COPY config.yaml /app/config.yaml
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/run.py /usr/local/bin/parse_results_stats.py /usr/local/bin/download_data.py /usr/local/bin/upload_results.py

WORKDIR /app
RUN rm -rf /app/.venv && \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python uv sync --frozen --no-dev

# Ensure venv is accessible to hms user
RUN chown -R hms:hms /app /opt/uv-python && \
    chmod -R a+rX /app/.venv /opt/uv-python

# Note: We run as root to allow writing to mounted directories
# The entrypoint will handle permissions for files created

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
