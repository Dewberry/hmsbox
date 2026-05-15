#!/bin/bash

# HMS Notebook Batch Processor
# Runs the HMS Analysis notebook and exports to HTML

set -e

OUTPUT_DIR="${OUTPUT_DIR:-.}"
NOTEBOOK_PATH="${NOTEBOOK_PATH:-HMS_Analysis.ipynb}"
BASE_NAME="${BASE_NAME:-HMS_Analysis_Report}"

echo "=========================================="
echo "Running HMS Analysis Notebook"
echo "=========================================="
echo ""

if [ ! -f "$NOTEBOOK_PATH" ]; then
    echo "Error: Notebook not found at $NOTEBOOK_PATH"
    exit 1
fi

echo "Input notebook: $NOTEBOOK_PATH"
echo "Output directory: $OUTPUT_DIR"
echo ""

# Run the notebook and save output
echo "Executing notebook..."
jupyter nbconvert \
    --to notebook \
    --execute \
    --ExecutePreprocessor.timeout=3600 \
    --output="$OUTPUT_DIR/${BASE_NAME}_executed.ipynb" \
    "$NOTEBOOK_PATH"

# Read init_time written by the notebook cell (e.g. 2026-03-11-00)
INIT_TIME=""
if [ -f "$OUTPUT_DIR/init_time.txt" ]; then
    INIT_TIME=$(cat "$OUTPUT_DIR/init_time.txt")
fi
if [ -n "$INIT_TIME" ]; then
    OUTPUT_NAME="${BASE_NAME}_${INIT_TIME}"
else
    OUTPUT_NAME="$BASE_NAME"
fi

echo ""
echo "Converting to HTML..."
jupyter nbconvert \
    --to html \
    --output="$OUTPUT_DIR/${OUTPUT_NAME}.html" \
    --no-input \
    --template lab \
    "$OUTPUT_DIR/${BASE_NAME}_executed.ipynb"

echo ""
echo "=========================================="
echo "✓ Notebook processing complete!"
echo "=========================================="
echo ""
echo "Output files:"
echo "  - Executed notebook: $OUTPUT_DIR/${BASE_NAME}_executed.ipynb"
echo "  - HTML report: $OUTPUT_DIR/${OUTPUT_NAME}.html"
echo ""
echo "HTML report: ${OUTPUT_NAME}.html"
