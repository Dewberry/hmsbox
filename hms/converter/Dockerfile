FROM python:3.12-slim

LABEL org.opencontainers.image.title="HEC-HMS DSS Converter" \
      org.opencontainers.image.description="Convert HEC-DSS files to/from Parquet format" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.vendor="Dewberry" \
      org.opencontainers.image.source="https://github.com/Dewberry/hmsbox" \
      org.opencontainers.image.documentation="https://github.com/Dewberry/hmsbox/tree/main/hms/converter"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY schemas/ schemas/
COPY entrypoint.sh /app/entrypoint.sh
RUN uv sync --frozen --no-dev && chmod +x /app/entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["/app/entrypoint.sh"]
