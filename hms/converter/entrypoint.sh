#!/bin/bash
# Entrypoint wrapper that filters DSS library log messages unless --verbose is specified

# Check if --verbose or -v flag is present
if [[ "$@" =~ (--verbose|-v[^a-z]) ]]; then
    # Verbose mode: show all output including DSS messages
    exec python -m src.convert "$@"
else
    # Default quiet mode: filter out DSS debug messages
    python -m src.convert "$@" 2>&1 | grep -v -E '(-----DSS---|Handle [0-9]+|Process:|DSS Versions|Single-user advisory|Number records:|File size:|Dead space:|Hash range:|Number hash used:|Max paths for hash:|Corresponding hash:|Number non unique hash:|Number bins used:|Number overflow bins:|Number physical reads:|Number physical writes:|Number denied locks:|Existing file opened|Version [0-9]+:|zopen|zread|zclose)' || true

    # Preserve the exit code from python, not grep
    exit ${PIPESTATUS[0]}
fi
