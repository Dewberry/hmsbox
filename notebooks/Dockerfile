FROM python:3.12-slim

LABEL org.opencontainers.image.title="HMS Analysis Notebook" \
      org.opencontainers.image.description="Jupyter notebook for HMS forecast analysis and visualization" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.vendor="Dewberry" \
      org.opencontainers.image.source="https://github.com/Dewberry/hmsbox"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Create pyproject.toml with notebook dependencies
RUN cat > pyproject.toml << 'EOF'
[project]
name = "hms-analysis-notebook"
version = "0.1.0"
description = "HMS forecast analysis and visualization notebook"
requires-python = ">=3.12"
dependencies = [
    "jupyter>=1.0",
    "jupyterlab>=4.0",
    "nbconvert>=7.0",
    "pandas>=3.0.1",
    "numpy>=1.24.0",
    "matplotlib>=3.8.0",
    "xarray>=2024.0.0",
    "netcdf4>=1.6.0",
    "geopandas>=1.0.0",
    "folium>=0.14.0",
    "pyarrow>=23.0.1",
    "plotly>=5.18.0",
    "imageio>=2.33.0",
    "Pillow>=10.0.0"
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
EOF

RUN uv sync --no-dev --no-install-project

# Copy notebook entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /notebooks
ENTRYPOINT ["/app/entrypoint.sh"]
