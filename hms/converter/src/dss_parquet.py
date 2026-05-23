"""Convert Parquet files to HEC-DSS format."""

import logging
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from hecdss import HecDss, RegularTimeSeries

try:
    from src.datehandler import infer_interval_from_timestamps
    from src.validate import load_json_schema
except ModuleNotFoundError:
    from datehandler import infer_interval_from_timestamps
    from validate import load_json_schema

logger = logging.getLogger(__name__)

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

    # Add missing DSS parts with default values
    if "A" not in df_normalized.columns and "provider" in field_mapping:
        logger.debug("Adding missing provider (A) column with empty strings")
        df_normalized["A"] = ""

    return df_normalized, used_mapping


def _validate_parquet_columns(parquet_path: str) -> tuple[list[str], dict]:
    """Validate parquet and normalize columns. Returns required columns and used field mapping."""
    table = pq.read_table(parquet_path)
    columns = set(table.column_names)

    # Get field mapping from iceberg schema
    field_mapping = ICEBERG_SCHEMA.get("fieldMapping", {})

    # Core iceberg semantic columns (timestamp, value, site_id, variable)
    # Provider is optional if not present
    core_semantic_cols = {"timestamp", "value", "site_id", "variable"}
    optional_semantic_cols = {"provider"}

    # Check if we have semantic column names (iceberg format)
    has_core_semantic = core_semantic_cols.issubset(columns)

    if has_core_semantic:
        # Will normalize from semantic names to DSS parts
        logger.debug(
            "Detected iceberg-style semantic column names, will normalize to DSS parts"
        )

        # Build the active field mapping based on what's present
        active_mapping = {}
        for semantic_col, dss_part in field_mapping.items():
            if semantic_col in columns:
                active_mapping[semantic_col] = dss_part

        # Add provider as optional - if missing, it will be filled with empty string
        if "provider" not in columns and "provider" in field_mapping:
            logger.debug("Provider column missing from parquet, will use empty string")
            active_mapping["provider"] = field_mapping["provider"]

        return list(active_mapping.values()), active_mapping
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

    logger.debug(
        f"Starting parquet to DSS conversion: {parquet_path} -> {output_dss_path}"
    )

    # Validate parquet against schema
    columns, field_mapping = _validate_parquet_columns(parquet_path)
    df = pq.read_table(parquet_path).to_pandas()

    # Normalize columns if using iceberg schema
    if field_mapping:
        logger.debug(f"Normalizing iceberg columns to DSS parts: {field_mapping}")
        df, _ = _normalize_parquet(df, field_mapping)

    logger.debug(f"Loaded parquet with {len(df)} records")

    # Convert datetime column to datetime type if it's a string
    if "datetime" in df.columns and df["datetime"].dtype == "object":
        logger.debug("Converting datetime column from string to datetime")
        df["datetime"] = pd.to_datetime(df["datetime"])

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
        if part not in df.columns:
            df[part] = ""
        df[part] = df[part].fillna("")

    # Group by DSS path combination - use only A, B, C for grouping
    # (these are the semantic parts that define unique time series)
    groupby_cols = ["A", "B", "C"]
    if not all(col in df.columns for col in groupby_cols):
        logger.error(f"Missing required groupby columns: {groupby_cols}")
        return {"error": "Missing required DSS path parts", "converted": 0}

    # Create output directory
    Path(output_dss_path).parent.mkdir(parents=True, exist_ok=True)

    # Initialize DSS file
    try:
        with HecDss(output_dss_path) as dss:
            converted_count = 0

            # Group data by unique A/B/C combinations
            for group_vals, group_data in df.groupby(groupby_cols, sort=False):
                if not isinstance(group_vals, tuple):
                    group_vals = (group_vals,)

                # Build DSS path parts dictionary from groupby values
                # groupby_cols = ["A", "B", "C"], so order is preserved
                path_parts = {
                    col: str(val) for col, val in zip(groupby_cols, group_vals)
                }
                # Add D, E, F parts if they exist in data (take first value from group since they might be constant)
                if "D" in df.columns:
                    path_parts["D"] = (
                        str(group_data["D"].iloc[0]) if len(group_data) > 0 else ""
                    )
                if "E" in df.columns:
                    path_parts["E"] = (
                        str(group_data["E"].iloc[0]) if len(group_data) > 0 else ""
                    )
                if "F" in df.columns:
                    path_parts["F"] = (
                        str(group_data["F"].iloc[0]) if len(group_data) > 0 else ""
                    )

                # Sort by datetime
                group_data = group_data.sort_values("datetime")

                # Convert to times and values - ensure times are datetime objects
                times = pd.to_datetime(group_data["datetime"].tolist()).tolist()
                values = group_data["value"].tolist()

                logger.debug(f"First time: {times[0]}, First value: {values[0]}")

                # Determine interval: use E part if present, otherwise auto-detect
                # Note: interval detection is per unique group (provider/site_id/variable combination)
                interval = path_parts.get("E", None)
                if interval is None or interval == "":
                    # Auto-detect interval from timestamps for this specific group
                    detected_interval = infer_interval_from_timestamps(times)
                    if detected_interval is None:
                        logger.warning(
                            f"Could not detect interval for group {path_parts}, defaulting to 15Minute"
                        )
                        interval = "15Minute"
                    else:
                        logger.info(
                            f"Auto-detected interval: {detected_interval} for group {path_parts}"
                        )
                        interval = detected_interval
                    path_parts["E"] = interval

                # Get F part from data or use override
                # Determine F part based on provider
                a_part = path_parts.get("A", "")
                provider = a_part.upper() if a_part else ""

                # If no F part is provided, set to "gage" for USGS provider, blank otherwise
                f_part = path_f_part or path_parts.get("F", "")
                if not f_part:
                    f_part = "GAGE" if provider == "USGS" else ""

                # Build full DSS path: /A/B/C/D/E/F/ preserving structure
                # Ensure all 6 parts are present as positional elements
                b_part = path_parts.get("B", "")
                c_part = path_parts.get("C", "")
                d_part = path_parts.get("D", "")
                e_part = path_parts.get("E", "")
                f_part_final = f_part

                dss_path = (
                    f"/{a_part}/{b_part}/{c_part}/{d_part}/{e_part}/{f_part_final}/"
                )

                logger.info(
                    f"Writing DSS path: {dss_path} with {len(group_data)} records"
                )

                # Validate C part and determine units
                c_part = path_parts.get("C", "").upper()
                if c_part == "FLOW":
                    units = "CFS"
                elif c_part == "ELEVATION":
                    units = "FT"
                else:
                    logger.error(
                        f"Invalid C part '{c_part}'. Expected 'FLOW' (CFS) or 'ELEVATION' (FT)"
                    )
                    raise ValueError(
                        f"Invalid C part '{c_part}'. C part must be either 'FLOW' or 'ELEVATION', got '{c_part}'"
                    )

                # Write to DSS using RegularTimeSeries
                try:
                    # Create a RegularTimeSeries object
                    # For regular time series, pass both times array and start_date
                    ts = RegularTimeSeries.create(
                        values=values,
                        times=times,  # Pass datetime array
                        start_date=times[0],
                        interval=interval,
                        units=units,
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
