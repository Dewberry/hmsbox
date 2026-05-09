"""Convert HEC-DSS time series data to Parquet format."""

import json
import logging
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from multiprocessing import get_context
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
from hecdss import HecDss
from hecdss.dateconverter import DateConverter
from hecdss.dsspath import DssPath
from hecdss.hecdss import DSS_UNDEFINED_VALUE
from hecdss.record_type import RecordType

logger = logging.getLogger(__name__)

_MULTIPROCESSING_CONTEXT = get_context("spawn")


@contextmanager
def suppress_stdout_stderr():
    """No-op context manager. DSS output filtering is handled by entrypoint.sh wrapper."""
    yield


@contextmanager
def nullcontext():
    """No-op context manager."""
    yield


@dataclass(frozen=True)
class _TimeSeriesReadPlan:
    record_type: RecordType
    first_raw_path: str
    last_raw_path: str


@dataclass(frozen=True)
class _TimeSeriesData:
    id: str
    times: list
    values: list


@dataclass(frozen=True)
class _TableReadPlan:
    pass


@dataclass(frozen=True)
class _TableData:
    id: str
    dataframe: pd.DataFrame


def _path_to_parts(path: str) -> dict[str, str]:
    parts = path.split("/")
    return {
        "A": parts[1],
        "B": parts[2],
        "C": parts[3],
        "D": parts[4],
        "E": parts[5],
        "F": parts[6],
    }


def _raw_record_date(path: str) -> datetime:
    return datetime.strptime(DssPath(path).D, "%d%b%Y")


def _build_read_plans(
    catalog,
) -> tuple[dict[str, _TimeSeriesReadPlan], dict[str, _TableReadPlan]]:
    # `HecDss.get()` asks DSS to rediscover the full time window for every condensed
    # path, which is the main runtime cost for large exports. The catalog already
    # tells us which raw records belong to each condensed dataset, so we cache the
    # first and last raw paths once and reuse their bounds for every read.
    raw_bounds: dict[str, tuple[datetime, str, datetime, str, RecordType]] = {}
    for raw_path, raw_type in zip(catalog.uncondensed_paths, catalog.rawRecordTypes):
        record_type = RecordType.RecordTypeFromInt(raw_type)
        raw_dss_path = DssPath(raw_path, record_type)
        if not raw_dss_path.is_time_series():
            continue

        key = str(raw_dss_path.path_without_date())
        raw_date = _raw_record_date(raw_path)
        current = raw_bounds.get(key)
        if current is None:
            raw_bounds[key] = (raw_date, raw_path, raw_date, raw_path, record_type)
            continue

        min_date, min_path, max_date, max_path, existing_type = current
        if raw_date < min_date:
            min_date, min_path = raw_date, raw_path
        if raw_date > max_date:
            max_date, max_path = raw_date, raw_path
        raw_bounds[key] = (min_date, min_path, max_date, max_path, existing_type)

    ts_plans: dict[str, _TimeSeriesReadPlan] = {}
    table_plans: dict[str, _TableReadPlan] = {}
    for record in catalog:
        record_path = str(record)
        if record.is_time_series():
            min_date, min_path, max_date, max_path, record_type = raw_bounds[
                str(record.path_without_date())
            ]
            ts_plans[record_path] = _TimeSeriesReadPlan(
                record_type=record_type,
                first_raw_path=min_path,
                last_raw_path=max_path,
            )
        else:
            # Handle table records
            table_plans[record_path] = _TableReadPlan()
    return ts_plans, table_plans


def _dataset_range(dss: HecDss, plan: _TimeSeriesReadPlan) -> tuple[datetime, datetime]:
    first_start, _ = dss._get_date_time_range(plan.first_raw_path, 0)
    _, last_end = dss._get_date_time_range(plan.last_raw_path, 0)
    return first_start, last_end


def _read_timeseries_for_export(
    dss: HecDss,
    record_path: str,
    plan: _TimeSeriesReadPlan,
) -> _TimeSeriesData:
    # Use the cheap single-record bounds from the cached raw paths, then call the
    # same native retrieve path with an explicit window. This preserves the export
    # results while avoiding the expensive full-dataset window discovery done by
    # `HecDss.get()` for condensed paths.
    start_dt, end_dt = _dataset_range(dss, plan)
    start_date = start_dt.strftime("%d%b%Y")
    start_time = start_dt.strftime("%H:%M:%S")
    end_date = end_dt.strftime("%d%b%Y")
    end_time = end_dt.strftime("%H:%M:%S")

    start_minutes = DateConverter.julian_array_from_date_times([start_dt])[0]
    end_minutes = DateConverter.julian_array_from_date_times([end_dt])[0]
    start_seconds = [start_minutes % 1440 * 60]
    start_julian = [start_minutes // 1440]
    end_seconds = [end_minutes % 1440 * 60]
    end_julian = [end_minutes // 1440]

    number_values = [0]
    quality_element_size = [0]
    status = dss._native.hec_dss_tsGetSizes(
        record_path,
        start_date,
        start_time,
        end_date,
        end_time,
        number_values,
        quality_element_size,
    )
    if status != 0:
        raise RuntimeError(f"tsGetSizes failed for {record_path}: {status}")

    array_size = number_values[0]
    if plan.record_type == RecordType.RegularTimeSeries:
        interval_seconds = DateConverter.intervalString_to_sec(DssPath(record_path).E)
        array_size = dss._native.hec_dss_numberPeriods(
            interval_seconds,
            start_julian[0],
            start_seconds[0],
            end_julian[0],
            end_seconds[0],
        )

    times = [0]
    values: list[float] = []
    number_values_read = [0]
    quality: list[int] = []
    julian_base_date = [0]
    time_granularity_seconds = [0]
    units = [""]
    data_type = [""]
    time_zone_name = [""]

    status = dss._native.hec_dss_tsRetrieve(
        record_path,
        start_date,
        start_time,
        end_date,
        end_time,
        times,
        values,
        array_size + 1,
        number_values_read,
        quality,
        quality_element_size[0],
        julian_base_date,
        time_granularity_seconds,
        units,
        40,
        data_type,
        40,
        time_zone_name,
        40,
    )
    if status != 0:
        raise RuntimeError(f"tsRetrieve failed for {record_path}: {status}")

    if plan.record_type == RecordType.RegularTimeSeries:
        trimmed_indices = [
            i for i, value in enumerate(values) if value != DSS_UNDEFINED_VALUE
        ]
        if not trimmed_indices:
            times = []
            values = []
        else:
            start = trimmed_indices[0]
            end = trimmed_indices[-1] + 1
            times = times[start:end]
            values = values[start:end]

    new_times = DateConverter.date_times_from_julian_array(
        times,
        time_granularity_seconds[0],
        julian_base_date[0],
    )

    if plan.record_type == RecordType.IrregularTimeSeries:
        filtered_times = []
        filtered_values = []
        for dt_value, value in zip(new_times, values):
            if value == DSS_UNDEFINED_VALUE:
                continue
            filtered_times.append(dt_value)
            filtered_values.append(value)
        new_times = filtered_times
        values = filtered_values

    if time_zone_name[0]:
        try:
            zone = ZoneInfo(time_zone_name[0])
            new_times = [dt_value.replace(tzinfo=zone) for dt_value in new_times]
        except ZoneInfoNotFoundError:
            pass

    return _TimeSeriesData(id=record_path, times=new_times, values=values)


def _read_table_for_export(dss: HecDss, record_path: str) -> _TableData:
    """Read a TABLE (PairedData) record from DSS and return as a DataFrame.

    TABLE records in DSS are typically PairedData records containing paired ordinates
    (X values) and values (Y values), often representing curves or relationships.
    """
    try:
        table_record = dss.get(record_path)

        if table_record is None:
            logger.debug(f"Table record returned None: {record_path}")
            return _TableData(id=record_path, dataframe=pd.DataFrame())

        # Handle PairedData records
        if hasattr(table_record, "ordinates") and hasattr(table_record, "values"):
            # PairedData has ordinates (x-axis) and values (y-axis)
            ordinates = table_record.ordinates
            values = table_record.values

            # Convert to lists if they're numpy arrays or other types
            if ordinates is not None:
                ordinates = (
                    list(ordinates)
                    if hasattr(ordinates, "__iter__") and not isinstance(ordinates, str)
                    else [ordinates]
                )
            else:
                ordinates = []

            if values is not None:
                values = (
                    list(values)
                    if hasattr(values, "__iter__") and not isinstance(values, str)
                    else [values]
                )
            else:
                values = []

            logger.debug(
                f"PairedData: ordinates={len(ordinates)}, values={len(values)}"
            )

            # Check if we have data
            if not ordinates or not values or len(ordinates) == 0 or len(values) == 0:
                logger.debug(f"PairedData has empty ordinates or values: {record_path}")
                return _TableData(id=record_path, dataframe=pd.DataFrame())

            # Create DataFrame from paired data
            data_dict = {"ordinate": ordinates, "value": values}

            # Add labels if available
            if hasattr(table_record, "labels") and table_record.labels:
                labels = table_record.labels
                if len(labels) >= 2 and labels[0] and labels[1]:
                    data_dict = {labels[0]: ordinates, labels[1]: values}

            df = pd.DataFrame(data_dict)
            logger.debug(f"Created DataFrame with {len(df)} rows for {record_path}")
            if not df.empty:
                return _TableData(id=record_path, dataframe=df)

        # Fallback for other table types
        if hasattr(table_record, "__dict__"):
            df = pd.DataFrame([vars(table_record)])
            if not df.empty:
                return _TableData(id=record_path, dataframe=df)

        logger.debug(f"Could not extract data from table record: {record_path}")
        return _TableData(id=record_path, dataframe=pd.DataFrame())

    except Exception as e:
        logger.error(
            f"Error reading table record {record_path}: {type(e).__name__}: {e}"
        )
        import traceback

        logger.error(traceback.format_exc())
        return _TableData(id=record_path, dataframe=pd.DataFrame())


def _export_group_worker(
    group_key: str,
    records: list[str],
    ts_read_plans: dict[str, _TimeSeriesReadPlan],
    table_read_plans: dict[str, _TableReadPlan],
    dss_path: str,
    output_dir: str,
    include_parts: list[str],
    event_id: int | None,
    suppress_dss_output: bool = False,
    use_iceberg_schema: bool = True,
) -> tuple[str, str | None, int, list[str]]:
    data_lists: dict[str, list] = defaultdict(list)
    errors: list[str] = []

    context = suppress_stdout_stderr() if suppress_dss_output else nullcontext()

    with context:
        with HecDss(dss_path) as dss:
            for record_path in records:
                try:
                    parts = _path_to_parts(record_path)

                    # Check if this is a time series or table record
                    if record_path in ts_read_plans:
                        # Time series record
                        data = _read_timeseries_for_export(
                            dss, record_path, ts_read_plans[record_path]
                        )
                        n = len(data.times)
                        data_lists["datetime"].extend(data.times)
                        data_lists["value"].extend(data.values)
                        if event_id is not None:
                            data_lists["event_id"].extend([event_id] * n)
                        # Include DSS path parts based on include_parts parameter
                        # Always include required parts A, B, C
                        required_parts = ["A", "B", "C"]
                        for part in required_parts:
                            data_lists[part].extend([parts[part]] * n)
                        # Include additional parts from include_parts
                        for part in include_parts:
                            if part not in required_parts:
                                data_lists[part].extend([parts[part]] * n)
                    elif record_path in table_read_plans:
                        # Table record
                        table_data = _read_table_for_export(dss, record_path)
                        if table_data.dataframe.empty:
                            continue

                        # Add DSS path parts as constant columns
                        n = len(table_data.dataframe)
                        # Always include required parts A, B, C
                        required_parts = ["A", "B", "C"]
                        for part in required_parts:
                            table_data.dataframe[part] = parts[part]
                        if event_id is not None:
                            table_data.dataframe["event_id"] = event_id
                        # Include additional parts from include_parts
                        for part in include_parts:
                            if (
                                part not in required_parts
                                and part not in table_data.dataframe.columns
                            ):
                                table_data.dataframe[part] = parts[part]

                        # Add all columns from the table to the combined data
                        for col in table_data.dataframe.columns:
                            data_lists[col].extend(table_data.dataframe[col].tolist())
                    else:
                        errors.append(f"{record_path}: record not found in read plans")
                except Exception as e:
                    errors.append(f"{record_path}: {e}")

    if not data_lists or all(len(v) == 0 for v in data_lists.values()):
        return group_key, None, 0, errors

    df = pd.DataFrame(data_lists)
    if event_id is not None and "event_id" in df.columns:
        df["event_id"] = df["event_id"].astype("int32")

    # Apply iceberg schema column mapping if requested
    if use_iceberg_schema:
        iceberg_mapping = {
            "datetime": "timestamp",
            "A": "provider",
            "B": "site_id",
            "C": "variable",
            "E": "increment",
            "F": "qualifier",
        }
        # Only rename columns that exist in the DataFrame
        rename_dict = {
            old: new for old, new in iceberg_mapping.items() if old in df.columns
        }
        df = df.rename(columns=rename_dict)

    output_path = str(Path(output_dir) / f"{group_key}.parquet")
    try:
        df.to_parquet(output_path, index=False, compression="snappy")
    except Exception as e:
        errors.append(f"parquet write failed: {e}")
        return group_key, None, 0, errors
    return group_key, output_path, len(df), errors


def run(
    input_dss_path: str,
    output_path: str,
    groupby: str = "F",
    strip_suffix: bool = True,
    include_parts: list[str] | None = None,
    group_workers: int = 4,
    dss_workers: int = 1,
    event_id: int | None = None,
    suppress_dss_output: bool = False,
    use_iceberg_schema: bool = True,
) -> dict:
    """Convert a DSS file to Parquet format.

    Args:
        input_dss_path: Path to input DSS file
        output_path: Output file path for Parquet file
        groupby: DSS path part to group by (default: "F")
        strip_suffix: Strip version suffix from group keys (default: True)
        include_parts: DSS path parts to include as columns (default: ["F"])
        group_workers: Number of workers for group export parallelization (default: 4)
        dss_workers: Number of workers for DSS file parallelization (default: 1)
        event_id: Optional event ID to include in output
        suppress_dss_output: Suppress DSS library stdout/stderr output (default: False)
        use_iceberg_schema: Use iceberg schema column names (default: True)

    Returns:
        Manifest dict with export results
    """
    if include_parts is None:
        include_parts = ["F"]

    # Set global debug level for HecDss library (0 = minimal output, 1 = verbose)
    HecDss.set_global_debug_level(0 if suppress_dss_output else 1)

    logger.info(
        f"dss-to-parquet starting: input={input_dss_path}, output={output_path}, "
        f"groupby={groupby}, include_parts={include_parts}, event_id={event_id}, "
        f"group_workers={group_workers}, dss_workers={dss_workers}, "
        f"use_iceberg_schema={use_iceberg_schema}"
    )

    def _process_one(
        input_dss_file: str, output_file_path: str
    ) -> tuple[str, list[dict], int]:
        """Process one DSS input. Returns (dss_path, source_outputs, row_count).

        Errors within group exports are logged and reflected in an empty source_outputs.
        """
        dss_path = input_dss_file
        # dss_stem = Path(dss_path).stem

        try:
            Path(output_file_path).parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(
                f"Failed to create output directory: dss={dss_path}, path={output_file_path}, error={str(e)}"
            )
            return dss_path, [], 0

        logger.info(
            f"Processing DSS file: dss={dss_path}, size_bytes={Path(dss_path).stat().st_size}"
        )

        record_groups: dict[str, list[str]] = defaultdict(list)
        ts_read_plans: dict[str, _TimeSeriesReadPlan] = {}
        table_read_plans: dict[str, _TableReadPlan] = {}

        dss_context = suppress_stdout_stderr() if suppress_dss_output else nullcontext()

        try:
            with dss_context:
                with HecDss(dss_path) as dss:
                    catalog = dss.get_catalog()
                    ts_read_plans, table_read_plans = _build_read_plans(catalog)
                    for record in catalog:
                        try:
                            record_path = str(record)
                            parts = _path_to_parts(record_path)
                            val = parts[groupby]
                            key = (
                                val.split(":")[0]
                                if strip_suffix and ":" in val
                                else val
                            )
                            record_groups[key].append(record_path)
                        except Exception as e:
                            logger.warning(
                                f"Skipping record: dss={dss_path}, record={str(record)}, error={str(e)}"
                            )
        except Exception as e:
            import traceback

            logger.error(f"Failed to scan DSS catalog: dss={dss_path}, error={str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return dss_path, [], 0

        logger.info(
            f"Catalog scanned: dss={dss_path}, groups={len(record_groups)}, "
            f"records={sum(len(r) for r in record_groups.values())}, keys={sorted(record_groups)}"
        )

        out_tmp = Path(output_file_path).parent / "tmp"
        out_tmp.mkdir(parents=True, exist_ok=True)
        # Filter out empty group keys to avoid creating files with double dots
        groups = [(k, v) for k, v in sorted(record_groups.items()) if k]
        exec_mode = "sequential" if group_workers <= 1 else "process_pool"
        logger.info(
            f"Starting group exports: dss={dss_path}, groups={len(groups)}, "
            f"group_workers={group_workers}, execution_mode={exec_mode}"
        )

        source_outputs: list[dict] = []
        rows = 0

        if group_workers <= 1:
            results = []
            for key, records in groups:
                try:
                    results.append(
                        (
                            key,
                            _export_group_worker(
                                key,
                                records,
                                ts_read_plans,
                                table_read_plans,
                                dss_path,
                                str(out_tmp),
                                include_parts,
                                event_id,
                                suppress_dss_output=suppress_dss_output,
                                use_iceberg_schema=use_iceberg_schema,
                            ),
                        )
                    )
                except Exception as e:
                    logger.error(
                        f"Group export failed: dss={dss_path}, group={key}, error={str(e)}"
                    )
            for key, result in results:
                try:
                    gk, local_file, row_count, errors = result
                    for err in errors:
                        logger.warning(
                            f"Record error: dss={dss_path}, group={gk}, error={err}"
                        )
                    if local_file:
                        remote_path = output_file_path
                        Path(remote_path).parent.mkdir(parents=True, exist_ok=True)
                        if Path(local_file).exists():
                            shutil.copy(local_file, remote_path)
                        rows += row_count
                        source_outputs.append(
                            {"group": gk, "path": remote_path, "rows": row_count}
                        )
                        logger.info(
                            f"Group exported: dss={dss_path}, group={gk}, rows={row_count}, output={remote_path}"
                        )
                except Exception as e:
                    logger.error(
                        f"Group export failed: dss={dss_path}, group={key}, error={str(e)}"
                    )

            # Clean up temporary directory
            try:
                shutil.rmtree(out_tmp)
                logger.info(f"Cleaned up temporary directory: {out_tmp}")
            except Exception as e:
                logger.warning(f"Failed to clean up temporary directory {out_tmp}: {e}")
        else:
            with ProcessPoolExecutor(
                max_workers=group_workers,
                mp_context=_MULTIPROCESSING_CONTEXT,
            ) as executor:
                futures = {
                    executor.submit(
                        _export_group_worker,
                        key,
                        records,
                        ts_read_plans,
                        table_read_plans,
                        dss_path,
                        str(out_tmp),
                        include_parts,
                        event_id,
                        suppress_dss_output=suppress_dss_output,
                        use_iceberg_schema=use_iceberg_schema,
                    ): key
                    for key, records in groups
                }
                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        gk, local_file, row_count, errors = future.result()
                        for err in errors:
                            logger.warning(
                                f"Record error: dss={dss_path}, group={gk}, error={err}"
                            )
                        if local_file:
                            remote_path = output_file_path
                            Path(remote_path).parent.mkdir(parents=True, exist_ok=True)
                            if Path(local_file).exists():
                                shutil.copy(local_file, remote_path)
                            rows += row_count
                            source_outputs.append(
                                {"group": gk, "path": remote_path, "rows": row_count}
                            )
                            logger.info(
                                f"Group exported: dss={dss_path}, group={gk}, rows={row_count}, output={remote_path}"
                            )
                    except Exception as e:
                        logger.error(
                            f"Group export failed: dss={dss_path}, group={key}, error={str(e)}"
                        )

        # Clean up temporary directory
        try:
            shutil.rmtree(out_tmp)
            logger.info(f"Cleaned up temporary directory: {out_tmp}")
        except Exception as e:
            logger.warning(f"Failed to clean up temporary directory {out_tmp}: {e}")

        return dss_path, source_outputs, rows

    manifest_sources: list[dict] = []
    total_rows = 0

    dss_remote, source_outputs, rows = _process_one(input_dss_path, output_path)
    if dss_remote is not None:
        manifest_sources.append({"dss": dss_remote, "outputs": source_outputs})
    total_rows += rows

    manifest: dict = {}
    if event_id is not None:
        manifest["event_id"] = event_id
    manifest["sources"] = manifest_sources

    logger.info(f"dss-to-parquet complete: total_rows={total_rows}")

    return manifest
