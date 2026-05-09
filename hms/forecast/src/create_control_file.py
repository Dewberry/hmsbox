#!/usr/bin/env python3

import argparse
from datetime import datetime
from pathlib import Path
from forecast_logging import setup_json_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Forecast.control or Lookback.control file"
    )
    parser.add_argument(
        "control_type",
        nargs="?",
        default="Forecast",
        choices=["Forecast", "Lookback"],
        help="Control type to generate",
    )
    parser.add_argument("start_date", nargs="?", default="11 March 2026")
    parser.add_argument("start_time", nargs="?", default="01:00")
    parser.add_argument("end_date", nargs="?", default="11 March 2026")
    parser.add_argument("end_time", nargs="?", default="18:00")
    parser.add_argument("version", nargs="?", default="4.14")
    parser.add_argument("time_interval", nargs="?", default="60")
    parser.add_argument(
        "--output-dir",
        default="./model/Trinity_Forecast",
        help="Directory where the .control file is written",
    )
    return parser.parse_args()


def main() -> int:
    logger = setup_json_logging()
    args = parse_args()

    control_name = args.control_type
    output_dir = Path(args.output_dir)
    output_file = output_dir / f"{args.control_type}.control"

    now = datetime.now()
    last_mod_date = now.strftime("%d %B %Y")
    last_mod_time = now.strftime("%H:%M:%S")

    output_dir.mkdir(parents=True, exist_ok=True)

    content = (
        f"Control: {control_name}\n"
        f"     Last Modified Date: {last_mod_date}\n"
        f"     Last Modified Time: {last_mod_time}\n"
        f"     Version: {args.version}\n"
        f"     Start Date: {args.start_date}\n"
        f"     Start Time: {args.start_time}\n"
        f"     End Date: {args.end_date}\n"
        f"     End Time: {args.end_time}\n"
        f"     Time Interval: {args.time_interval}\n"
        "End:\n"
    )

    output_file.write_text(content, encoding="utf-8")
    logger.debug(f"Created control file: {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
