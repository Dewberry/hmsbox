#!/bin/bash
set -euo pipefail

# Run as root to allow writing to mounted directories
# After running, we'll fix permissions if needed
exec python3 /usr/local/bin/run.py "$@"
