#!/bin/bash
set -e

# If no arguments provided, start Jupyter Lab
if [ $# -eq 0 ]; then
    exec jupyter lab --ip=0.0.0.0 --allow-root --no-browser
else
    # Otherwise execute the provided command
    exec "$@"
fi
