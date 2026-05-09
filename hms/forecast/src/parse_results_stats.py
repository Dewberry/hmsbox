#!/usr/bin/env python3
"""
Parse HMS results XML and extract statistics to CSV or Parquet.
"""

import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_results_xml(xml_file):
    """Parse HMS results XML and extract key statistics."""

    tree = ET.parse(xml_file)
    root = tree.getroot()

    # Top level run information
    run_info = {
        "RunName": root.findtext("RunName"),
        "Description": root.findtext("Description"),
        "ExecutionTime": root.findtext("ExecutionTime"),
        "StartTime": root.findtext("StartTime"),
        "EndTime": root.findtext("EndTime"),
    }

    # Extract basin elements and their statistics
    rows = []

    for basin_elem in root.findall("BasinElement"):
        elem_name = basin_elem.get("name")
        elem_type = basin_elem.get("type")

        # Get basin level info
        area_elem = basin_elem.find("SubbasinArea")
        area = area_elem.get("area") if area_elem is not None else None
        units = area_elem.get("units") if area_elem is not None else None

        # Get each statistic measure
        for stat in basin_elem.findall(".//Statistics/StatisticMeasure"):
            stat_type = stat.get("type")
            display_str = stat.get("displayString")
            value = stat.get("value")
            stat_units = stat.get("units")

            row = {
                "BasinName": elem_name,
                "BasinType": elem_type,
                "Area": area,
                "AreaUnits": units,
                "StatisticType": stat_type,
                "DisplayName": display_str,
                "Value": value,
                "Units": stat_units,
                "RunName": run_info["RunName"],
                "ExecutionTime": run_info["ExecutionTime"],
            }
            rows.append(row)

    return run_info, rows


def save_to_csv(rows, output_file):
    """Save extracted data to CSV file."""
    if not rows:
        print("No data to write")
        return

    fieldnames = rows[0].keys()

    with open(output_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved {len(rows)} rows to {output_file}")


def save_to_parquet(rows, output_file):
    """Save extracted data to Parquet file using PyArrow."""
    if not rows:
        print("No data to write")
        return

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        print("Error: PyArrow not installed. Install with: pip install pyarrow")
        sys.exit(1)

    # Convert rows to pyarrow table
    table = pa.Table.from_pylist(rows)

    # Write parquet file
    pq.write_table(table, output_file)

    print(f"Saved {len(rows)} rows to {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 parse_results_stats.py <xml_file> [output_file]")
        print("Output format determined by file extension (.csv or .parquet)")
        sys.exit(1)

    xml_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "stats.parquet"

    if not Path(xml_file).exists():
        print(f"Error: XML file not found: {xml_file}")
        sys.exit(1)

    run_info, rows = parse_results_xml(xml_file)

    print(f"Run: {run_info['RunName']}")
    print(f"Extracted {len(rows)} time series entries")

    # Determine output format by file extension
    if output_file.endswith(".parquet"):
        save_to_parquet(rows, output_file)
    else:
        save_to_csv(rows, output_file)
