#!/usr/bin/env python3
"""
HMS Notebook Batch Processor
Executes HMS_Analysis.ipynb, exports to HTML, then uploads both to S3.

Environment variables:
  OUTPUT_DIR        Local output directory (default: current directory)
  NOTEBOOK_PATH     Path to input notebook (default: HMS_Analysis.ipynb)
  BASE_NAME         Base name for output files (default: HMS_Analysis_Report)
  HMS_S3_BUCKET     S3 bucket for uploads — skips upload if not set
  S3_RESULTS_PREFIX S3 key prefix, no trailing slash
                    (default: staging/temporary/results)

Usage:
  python run-notebook.py
  python run-notebook.py --notebook HMS_Analysis.ipynb --output-dir /tmp/out
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3


def run(cmd: list[str], desc: str) -> None:
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\nERROR: {desc} failed (exit {result.returncode})", file=sys.stderr)
        sys.exit(result.returncode)


def s3_upload(local_path: Path, bucket: str, key: str) -> None:
    s3 = boto3.client("s3")
    s3.upload_file(str(local_path), bucket, key)
    print(f"  ✓ {local_path.name} → s3://{bucket}/{key}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--notebook", default=os.getenv("NOTEBOOK_PATH", "HMS_Analysis.ipynb")
    )
    parser.add_argument("--output-dir", default=os.getenv("OUTPUT_DIR", "."))
    parser.add_argument(
        "--base-name", default=os.getenv("BASE_NAME", "HMS_Analysis_Report")
    )
    parser.add_argument(
        "--s3-bucket", default=os.getenv("HMS_S3_BUCKET", "flood-warning")
    )
    parser.add_argument(
        "--s3-prefix",
        default=os.getenv("S3_RESULTS_PREFIX", "staging/temporary/results"),
    )
    args = parser.parse_args()

    notebook_path = Path(args.notebook)
    output_dir = Path(args.output_dir)
    base_name = args.base_name

    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 50)
    print("Running HMS Analysis Notebook")
    print("=" * 50)
    print(f"  Input:      {notebook_path}")
    print(f"  Output dir: {output_dir}")
    print()

    if not notebook_path.exists():
        print(f"ERROR: Notebook not found: {notebook_path}", file=sys.stderr)
        sys.exit(1)

    # --- Execute notebook ---
    executed_ipynb = output_dir / f"{base_name}_executed.ipynb"
    print("Executing notebook...")
    run(
        [
            "jupyter",
            "nbconvert",
            "--to",
            "notebook",
            "--execute",
            "--ExecutePreprocessor.timeout=3600",
            f"--output={executed_ipynb}",
            str(notebook_path),
        ],
        "notebook execution",
    )

    # --- Derive timestamped output name from init_time.txt ---
    init_time_file = output_dir / "init_time.txt"
    init_time = init_time_file.read_text().strip() if init_time_file.exists() else ""
    output_name = f"{base_name}_{init_time}" if init_time else base_name

    # --- Export to HTML ---
    html_output = output_dir / f"{output_name}.html"
    print("\nConverting to HTML...")
    run(
        [
            "jupyter",
            "nbconvert",
            "--to",
            "html",
            f"--output={html_output}",
            "--no-input",
            "--template",
            "lab",
            str(executed_ipynb),
        ],
        "HTML export",
    )

    print()
    print("=" * 50)
    print("✓ Notebook processing complete!")
    print("=" * 50)
    print(f"  Executed notebook: {executed_ipynb}")
    print(f"  HTML report:       {html_output}")

    # --- Upload to S3 ---
    if not args.s3_bucket:
        print("\nHMS_S3_BUCKET not set — skipping S3 upload.")
        return

    # Build S3 key prefix from init_time components (YYYY-MM-DD-HH).
    # Fall back to current UTC hour if init_time.txt was not written.
    if init_time and len(init_time) >= 13:
        year, month, day, hour = (
            init_time[0:4],
            init_time[5:7],
            init_time[8:10],
            init_time[11:13],
        )
    else:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        year, month, day, hour = (
            f"{now.year}",
            f"{now.month:02d}",
            f"{now.day:02d}",
            f"{now.hour:02d}",
        )
    s3_prefix = f"{args.s3_prefix}/{year}/{month}/{day}/{hour}"

    print(f"\nUploading to s3://{args.s3_bucket}/{s3_prefix}/")
    s3_upload(executed_ipynb, args.s3_bucket, f"{s3_prefix}/{executed_ipynb.name}")
    s3_upload(html_output, args.s3_bucket, f"{s3_prefix}/{html_output.name}")
    print(f"\nHTML report: {output_name}.html")


if __name__ == "__main__":
    main()
