"""Convert Parquet files to HEC-DSS format."""

import logging
from pathlib import Path

import pyarrow.parquet as pq
from hecdss import HecDss, RegularTimeSeries

try:
    from src.validate import load_json_schema
except ModuleNotFoundError:
    from validate import load_json_schema

logger = logging.getLogger(__name__)
# Suppress module logger output by default; structured JSON logging is handled in convert.py
# Can be re-enabled by setting logger level externally
# logger.setLevel(logging.CRITICAL + 1)

_DSS_SCHEMA_FILE = (
    Path(__file__).resolve().parent.parent / "schemas" / "dss-schema.json"
)

_ICEBERG_SCHEMA_FILE = (
    Path(__file__).resolve().parent.parent / "schemas" / "iceberg-schema.json"
)

DSS_SCHEMA: dict = load_json_schema(_DSS_SCHEMA_FILE)
ICEBERG_SCHEMA: dict = load_json_schema(_ICEBERG_SCHEMA_FILE)


def _normalize_parquet(df, field_mapping: dict) -> tuple:
    """Normalize parquet dataframe columns using field mapping, return normalized df and used mapping."""
    # Check which fields are present in the dataframe
    available_fields = set(df.columns)

    # Try to match iceberg semantic names to generic DSS parts
    used_mapping = {}
    for semantic_col, dss_part in field_mapping.items():
        if semantic_col in available_fields:
            used_mapping[semantic_col] = dss_part

    # Rename columns based on the mapping
    rename_map = {}
    for semantic_col, dss_part in used_mapping.items():
        rename_map[semantic_col] = dss_part

    df_normalized = df.rename(columns=rename_map)
    return df_normalized, used_mapping


def _validate_parquet_columns(parquet_path: str) -> tuple[list[str], dict]:
    """Validate parquet and normalize columns. Returns required columns and used field mapping."""
    table = pq.read_table(parquet_path)
    columns = set(table.column_names)

    # Get field mapping from iceberg schema
    field_mapping = ICEBERG_SCHEMA.get("fieldMapping", {})

    # Check if we have semantic column names (iceberg format)
    iceberg_required = set(ICEBERG_SCHEMA.get("required", []))
    has_semantic_columns = iceberg_required.issubset(columns)

    if has_semantic_columns:
        # Will normalize from semantic names to DSS parts
        logger.debug(
            "Detected iceberg-style semantic column names, will normalize to DSS parts"
        )
        return list(field_mapping.values()), field_mapping
    else:
        # Expect generic DSS column names already (A, B, C, D, E, F, datetime, value)
        required_fields = set(DSS_SCHEMA.get("required", []))
        missing = required_fields - columns
        if missing:
            raise ValueError(f"Parquet missing required columns: {sorted(missing)}")
        return sorted(required_fields), {}


def parquet_to_dss(
    parquet_path: str,
    output_dss_path: str,
    path_f_part: str = None,
    suppress_dss_output: bool = False,
) -> dict:
    """
    Convert parquet file to HEC-DSS format.

    Args:
        parquet_path: Path to input parquet file
        output_dss_path: Path to output DSS file
        path_f_part: Optional F part for DSS path (e.g., "15Minute")
        suppress_dss_output: Suppress DSS library stdout/stderr output (default: False)

    Returns:
        Manifest with conversion details
    """
    # Set global debug level for HecDss library (0 = minimal output, 1 = verbose)
    HecDss.set_global_debug_level(0 if suppress_dss_output else 1)

    logger.info(
        f"Starting parquet to DSS conversion: {parquet_path} -> {output_dss_path}"
    )

    # Validate parquet against schema
    columns, field_mapping = _validate_parquet_columns(parquet_path)
    df = pq.read_table(parquet_path).to_pandas()

    # Normalize columns if using iceberg schema
    if field_mapping:
        logger.info(f"Normalizing iceberg columns to DSS parts: {field_mapping}")
        df, _ = _normalize_parquet(df, field_mapping)

    logger.info(f"Loaded parquet with {len(df)} records")

    # Get required DSS parts from schema
    required_dss_parts = [
        part
        for part in DSS_SCHEMA.get("required", [])
        if part not in ("datetime", "value")
    ]

    # Also include optional DSS parts if they exist in the data
    optional_dss_parts = ["D", "E", "F"]
    existing_optional = [part for part in optional_dss_parts if part in df.columns]

    dss_parts = required_dss_parts + existing_optional
    logger.debug(
        f"DSS parts - required: {required_dss_parts}, optional present: {existing_optional}"
    )

    # Fill missing values in all DSS parts
    for part in dss_parts:
        df[part] = df[part].fillna("")

    # Group by DSS path combination
    groupby_cols = dss_parts
    if not groupby_cols:
        logger.error(f"No DSS path parts found in parquet")
        return {"error": "No DSS path parts found", "converted": 0}

    # Create output directory
    Path(output_dss_path).parent.mkdir(parents=True, exist_ok=True)

    # Initialize DSS file
    try:
        with HecDss(output_dss_path) as dss:
            converted_count = 0

            # Group data by unique path combinations
            for group_vals, group_data in df.groupby(groupby_cols, sort=False):
                if not isinstance(group_vals, tuple):
                    group_vals = (group_vals,)

                # Build DSS path
                path_parts = {
                    col: str(val) for col, val in zip(groupby_cols, group_vals)
                }

                # Get F part from data or use override
                # If no F part is provided and no qualifier in data, use E part (interval) as F part
                f_part = path_f_part or path_parts.get("F", path_parts.get("E", ""))

                # Build full DSS path: /A/B/C/D/E/F/
                dss_path = (
                    f"/{path_parts.get('A', '')}/{path_parts.get('B', '')}/"
                    + f"{path_parts.get('C', '')}/{path_parts.get('D', '')}/"
                    + f"{path_parts.get('E', '')}/{f_part}/"
                )
                dss_path = dss_path.strip("/")
                dss_path = f"/{dss_path}/"

                logger.info(
                    f"Writing DSS path: {dss_path} with {len(group_data)} records"
                )

                # Sort by datetime
                group_data = group_data.sort_values("datetime")

                # Convert to times and values
                times = group_data["datetime"].tolist()
                values = group_data["value"].tolist()

                logger.debug(f"First time: {times[0]}, First value: {values[0]}")

                # Write to DSS using RegularTimeSeries
                try:
                    # Create a RegularTimeSeries object
                    # For regular time series, pass both times array and start_date
                    ts = RegularTimeSeries.create(
                        values=values,
                        times=times,  # Pass datetime array
                        start_date=times[0],
                        interval=path_parts.get(
                            "E", "15MIN"
                        ),  # Use interval from E part
                        units="CFS",
                        data_type="INST-VAL",
                        path=dss_path,
                    )

                    # Store to DSS file
                    dss.put(ts)
                    converted_count += 1
                    logger.info(
                        f"Successfully wrote {len(values)} values to {dss_path}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to write DSS path {dss_path}: {e}", exc_info=True
                    )

        logger.info(
            f"Parquet to DSS conversion complete: {converted_count} timeseries written"
        )
        return {
            "input": parquet_path,
            "output": output_dss_path,
            "converted": converted_count,
            "total_records": len(df),
        }

    except Exception as e:
        logger.error(f"DSS file operation failed: {e}", exc_info=True)
        return {"error": str(e), "converted": 0}
