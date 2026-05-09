# Declare HMS_VERSION globally before any FROM
ARG HMS_VERSION=4.14-beta.1

# Use the existing HMS container as base
FROM hmsbox-headless:${HMS_VERSION}

LABEL org.opencontainers.image.title="HEC-HMS Forecast Container" \
      org.opencontainers.image.description="HMS container with Python for DSS conversion"

USER root

# Install Python 3
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Copy Python environment and source from pre-built DSS image
COPY --from=hmsbox-converter:latest /app /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv


# COPY src/entrypoint.py /usr/local/bin/entrypoint.py
# COPY src/entrypoint_logging.py /usr/local/bin/entrypoint_logging.py
# COPY src/entrypoint_time.py /usr/local/bin/entrypoint_time.py
# COPY src/parse_results_stats.py /usr/local/bin/parse_results_stats.py
COPY src/ /usr/local/bin/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/run.py /usr/local/bin/parse_results_stats.py

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
