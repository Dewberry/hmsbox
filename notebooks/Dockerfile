FROM python:3.12-slim

LABEL org.opencontainers.image.title="HMS Analysis Notebook" \
      org.opencontainers.image.description="Jupyter notebook for HMS forecast analysis and visualization" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.vendor="Dewberry" \
      org.opencontainers.image.source="https://github.com/Dewberry/hmsbox"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
RUN uv sync --no-dev --no-install-project

# Copy notebook entrypoint and runner
COPY entrypoint.sh /app/entrypoint.sh
COPY run-notebook.py /notebooks/run-notebook.py
COPY HMS_Analysis.ipynb /notebooks/HMS_Analysis.ipynb
COPY hms_analysis.py /notebooks/hms_analysis.py
RUN chmod +x /app/entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /notebooks
ENTRYPOINT ["/app/entrypoint.sh"]
