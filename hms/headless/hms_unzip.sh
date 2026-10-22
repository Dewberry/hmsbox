#!/bin/bash
# 
# Minimally unzip the HEC-HMS software as downloaded.
# 

set -euo pipefail

if [[ $# -ne 2 ]]; then
    printf 'Usage: hms_unzip.sh <HMS-archive> <install-directory>\n' >&2
    exit 2
fi

archive_path=$1
install_dir=$2
[[ -f "$archive_path" ]] || { printf 'HMS archive not found: %s\n' "$archive_path" >&2; exit 1; }

mkdir -p "$install_dir"
tar -xzf "$archive_path" --strip-components=1 -C "$install_dir" \
    --exclude='*.zip' \
    --exclude='*.exe' \
    --exclude='*.bat' \
    --exclude='*.cmd' \
    --exclude='*/samples' \
    --exclude='*/docs' \
    --exclude='*/examples' \
    --exclude='*/vortex'
chmod +x "${install_dir}/hec-hms.sh" "${install_dir}/jre/bin/java"
rm -f "$archive_path"
