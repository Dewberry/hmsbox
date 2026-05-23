#!/bin/bash
set -e

# If no arguments provided, run the notebook batch processor (ECS / cron mode).
# Pass "lab" as the first argument to start an interactive Jupyter Lab server instead.
if [ $# -eq 0 ]; then
    exec python /notebooks/run-notebook.py
elif [ "$1" = "lab" ]; then
    exec jupyter lab --ip=0.0.0.0 --allow-root --no-browser
else
    exec "$@"
fi
