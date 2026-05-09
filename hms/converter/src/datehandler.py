"""Time series interval detection for HEC-DSS."""

import logging
from datetime import datetime, timedelta
from collections import Counter
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Valid HEC-DSS time interval identifiers (from hecdss library)
_DSS_TIME_INTERVALS = [
    "1Year",
    "1Month",
    "Semi-Month",
    "Tri-Month",
    "1Week",
    "1Day",
    "12Hour",
    "8Hour",
    "6Hour",
    "4Hour",
    "3Hour",
    "2Hour",
    "1Hour",
    "30Minute",
    "20Minute",
    "15Minute",
    "12Minute",
    "10Minute",
    "6Minute",
    "5Minute",
    "4Minute",
    "3Minute",
    "2Minute",
    "1Minute",
    "30Second",
    "20Second",
    "15Second",
    "10Second",
    "6Second",
    "5Second",
    "4Second",
    "3Second",
    "2Second",
    "1Second",
    "0Second",
]

# Mapping of DSS interval strings to timedelta (for sub-day intervals)
_INTERVAL_TO_TIMEDELTA = {
    "12Hour": timedelta(hours=12),
    "8Hour": timedelta(hours=8),
    "6Hour": timedelta(hours=6),
    "4Hour": timedelta(hours=4),
    "3Hour": timedelta(hours=3),
    "2Hour": timedelta(hours=2),
    "1Hour": timedelta(hours=1),
    "30Minute": timedelta(minutes=30),
    "20Minute": timedelta(minutes=20),
    "15Minute": timedelta(minutes=15),
    "12Minute": timedelta(minutes=12),
    "10Minute": timedelta(minutes=10),
    "6Minute": timedelta(minutes=6),
    "5Minute": timedelta(minutes=5),
    "4Minute": timedelta(minutes=4),
    "3Minute": timedelta(minutes=3),
    "2Minute": timedelta(minutes=2),
    "1Minute": timedelta(minutes=1),
    "30Second": timedelta(seconds=30),
    "20Second": timedelta(seconds=20),
    "15Second": timedelta(seconds=15),
    "10Second": timedelta(seconds=10),
    "6Second": timedelta(seconds=6),
    "5Second": timedelta(seconds=5),
    "4Second": timedelta(seconds=4),
    "3Second": timedelta(seconds=3),
    "2Second": timedelta(seconds=2),
    "1Second": timedelta(seconds=1),
    "0Second": timedelta(seconds=0),
}


def infer_interval_from_timestamps(timestamps: List[datetime]) -> Optional[str]:
    """
    Infer the most likely DSS interval from a list of timestamps.

    Args:
        timestamps: List of datetime objects (should be sorted)

    Returns:
        DSS interval string (e.g., "15Minute", "1Hour") or None if cannot determine
    """
    if len(timestamps) < 2:
        logger.warning("Need at least 2 timestamps to infer interval")
        return None

    # Sort timestamps to ensure correct order
    timestamps = sorted(timestamps)

    # Calculate time differences between consecutive timestamps
    diffs = []
    for i in range(1, len(timestamps)):
        diff = timestamps[i] - timestamps[i - 1]
        # Only consider positive differences
        if diff.total_seconds() > 0:
            diffs.append(diff)

    if not diffs:
        logger.warning("No valid time differences found")
        return None

    # Find most common difference (mode)
    diff_counter = Counter(diffs)
    most_common_diff, count = diff_counter.most_common(1)[0]

    # Check if this is a consistent interval (>80% of differences match)
    consistency = count / len(diffs)
    if consistency < 0.8:
        logger.warning(
            f"Inconsistent time intervals detected (consistency: {consistency:.1%})"
        )

    # Match the difference to a DSS interval
    # Try exact match first
    for interval_name, interval_delta in _INTERVAL_TO_TIMEDELTA.items():
        if most_common_diff == interval_delta:
            logger.debug(f"Detected interval: {interval_name} (exact match)")
            return interval_name

    # Try approximate match (within 1 second tolerance)
    seconds_diff = most_common_diff.total_seconds()
    for interval_name, interval_delta in _INTERVAL_TO_TIMEDELTA.items():
        interval_seconds = interval_delta.total_seconds()
        if abs(seconds_diff - interval_seconds) <= 1:
            logger.debug(
                f"Detected interval: {interval_name} (approximate match, "
                f"actual: {seconds_diff}s)"
            )
            return interval_name

    # Check for daily or longer intervals
    days_diff = most_common_diff.days
    if days_diff == 1:
        return "1Day"
    elif days_diff == 7:
        return "1Week"

    logger.warning(
        f"Could not match time difference {most_common_diff} to a DSS interval"
    )
    return None


def infer_intervals_from_dataframe(
    df: pd.DataFrame,
    datetime_col: str = "datetime",
    group_cols: List[str] = None,
) -> dict:
    """
    Infer time intervals for each group in a dataframe.

    Args:
        df: DataFrame with time series data
        datetime_col: Name of datetime column
        group_cols: Columns to group by (e.g., ["A", "B", "C"] or ["provider", "site_id", "variable"])

    Returns:
        Dictionary mapping group tuples to interval strings
        Example: {('TRINITY RV', 'OAKWOOD, TX', 'FLOW'): '15Minute'}
    """
    if group_cols is None:
        group_cols = ["A", "B", "C"]

    intervals = {}

    # Group by the specified columns
    for group_vals, group_data in df.groupby(group_cols, sort=False):
        if not isinstance(group_vals, tuple):
            group_vals = (group_vals,)

        # Sort by datetime
        group_data = group_data.sort_values(datetime_col)
        timestamps = group_data[datetime_col].tolist()

        # Infer interval
        interval = infer_interval_from_timestamps(timestamps)

        if interval:
            intervals[group_vals] = interval
            logger.debug(f"Group {group_vals}: interval={interval}")
        else:
            logger.warning(f"Could not determine interval for group {group_vals}")

    return intervals


def get_interval_for_group(
    df: pd.DataFrame,
    group_vals: tuple,
    datetime_col: str = "datetime",
    group_cols: List[str] = None,
    default: str = "15Minute",
) -> str:
    """
    Get the interval for a specific group in the dataframe.

    Args:
        df: DataFrame with time series data
        group_vals: Tuple of group values to filter by
        datetime_col: Name of datetime column
        group_cols: Columns to group by
        default: Default interval if detection fails

    Returns:
        DSS interval string
    """
    if group_cols is None:
        group_cols = ["A", "B", "C"]

    # Filter to this group
    mask = True
    for col, val in zip(group_cols, group_vals):
        mask = mask & (df[col] == val)

    group_data = df[mask].sort_values(datetime_col)
    timestamps = group_data[datetime_col].tolist()

    interval = infer_interval_from_timestamps(timestamps)

    if interval:
        return interval
    else:
        logger.warning(
            f"Could not determine interval for group {group_vals}, using default: {default}"
        )
        return default
