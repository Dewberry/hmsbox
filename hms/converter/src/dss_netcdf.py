#!/usr/bin/env python
"""
Fast creation of gridded forecast NetCDF using grid indices directly.
No interpolation needed - each location maps to specific grid cells via indices.
"""

import logging
from datetime import datetime
from pathlib import Path

import h5py
import netCDF4
import numpy as np

logger = logging.getLogger(__name__)


def build_grid_mapping(h5_file):
    """
    Build mapping from grid indices to coordinates.
    Assigns a unique sequential ID to each grid cell.

    Note: HMS grid_indices are stored as [col, row] not [row, col].
    We swap them to use standard (row, col) convention.

    Returns:
        grid_index_to_cellid: dict mapping (row, col) -> cell_id
        grid_cellid_to_coord: dict mapping cell_id -> (lat, lon)
        row_col_to_cellid_array: 2D array for fast lookup
        row_min, row_max, col_min, col_max: grid dimensions
        location_grid_mapping: dict mapping location -> [(cell_id, data_index)]
    """
    logger.info("Building grid mapping...")

    with h5py.File(h5_file, "r") as f:
        ext_vars = f["external_variables"]
        location_names = sorted(ext_vars.keys())

        # Step 1: Collect all unique grid indices and assign cell IDs
        grid_index_to_cellid = {}
        grid_cellid_to_coord = {}
        cell_id = 0

        for loc_name in location_names:
            grp = ext_vars[loc_name]
            grid_indices = grp["grid_indices"][:]
            coordinates = grp["coordinates"][:]

            for idx_pair, coord_pair in zip(grid_indices, coordinates):
                # HMS stores as [col, row], swap to [row, col]
                key = (idx_pair[1], idx_pair[0])
                if key not in grid_index_to_cellid:
                    grid_index_to_cellid[key] = cell_id
                    grid_cellid_to_coord[cell_id] = coord_pair
                    cell_id += 1

        logger.info(f"Total unique grid cells: {cell_id}")

        # Step 2: Get grid dimensions
        all_rows = np.array([idx[0] for idx in grid_index_to_cellid.keys()])
        all_cols = np.array([idx[1] for idx in grid_index_to_cellid.keys()])

        row_min, row_max = all_rows.min(), all_rows.max()
        col_min, col_max = all_cols.min(), all_cols.max()

        logger.info(
            f"Grid dimensions: rows [{row_min}, {row_max}], cols [{col_min}, {col_max}]"
        )
        logger.info(f"  Span: {row_max - row_min + 1} x {col_max - col_min + 1}")

        # Step 3: Create 2D lookup array
        row_col_to_cellid_array = np.full(
            (row_max - row_min + 1, col_max - col_min + 1),
            fill_value=-1,
            dtype=np.int32,
        )

        for (row, col), cellid in grid_index_to_cellid.items():
            row_col_to_cellid_array[row - row_min, col - col_min] = cellid

        # Step 4: Map each location to its grid cells
        location_grid_mapping = {}

        for loc_name in location_names:
            grp = ext_vars[loc_name]
            grid_indices = grp["grid_indices"][:]

            cell_mapping = []
            for data_idx, idx_pair in enumerate(grid_indices):
                # HMS stores as [col, row], swap to [row, col]
                key = (idx_pair[1], idx_pair[0])
                cell_id = grid_index_to_cellid[key]
                cell_mapping.append((cell_id, data_idx))

            location_grid_mapping[loc_name] = cell_mapping

    return (
        grid_index_to_cellid,
        grid_cellid_to_coord,
        row_col_to_cellid_array,
        row_min,
        row_max,
        col_min,
        col_max,
        location_grid_mapping,
        location_names,
    )


def create_gridded_netcdf_fast(h5_file, output_file):
    """
    Create gridded NetCDF using grid indices (no interpolation).

    Output structure:
    - Dimensions: time, grid_row, grid_col, location
    - Variable: incremental_excess (time, grid_row, grid_col, location)
      where NaN indicates data not available at that grid cell for that location
    """
    logger.info("=" * 80)
    logger.info("STEP 1: Building grid mapping")
    logger.info("=" * 80)

    (
        grid_index_to_cellid,
        grid_cellid_to_coord,
        row_col_to_cellid_array,
        row_min,
        row_max,
        col_min,
        col_max,
        location_grid_mapping,
        location_names,
    ) = build_grid_mapping(h5_file)

    n_rows = row_max - row_min + 1
    n_cols = col_max - col_min + 1
    n_locations = len(location_names)
    location_id_map = {name: idx for idx, name in enumerate(location_names)}

    logger.info("\n" + "=" * 80)
    logger.info("STEP 2: Loading time data and determining data shape")
    logger.info("=" * 80)

    with h5py.File(h5_file, "r") as f:
        if "time_group" in f and "time60" in f["time_group"]:
            times = f["time_group"]["time60"][:].flatten()
        else:
            first_loc = location_names[0]
            n_times = f["results"][first_loc]["Incremental Excess"].shape[0]
            times = np.arange(n_times)

    n_times = len(times)
    logger.info(f"Timesteps: {n_times}")
    logger.info(f"Locations: {n_locations}")
    logger.info(f"Grid dimensions: {n_rows} rows × {n_cols} cols")

    logger.info("\n" + "=" * 80)
    logger.info("STEP 3: Reading all incremental excess data")
    logger.info("=" * 80)

    # Load all data into memory
    all_data = {}
    with h5py.File(h5_file, "r") as f:
        results = f["results"]
        for loc_name in location_names:
            all_data[loc_name] = results[loc_name]["Incremental Excess"][
                :
            ]  # (time, cells)

    logger.info("Data loaded into memory")

    logger.info("\n" + "=" * 80)
    logger.info("STEP 4: Creating NetCDF file")
    logger.info("=" * 80)

    ds = netCDF4.Dataset(output_file, "w", format="NETCDF4")

    try:
        # Create dimensions
        time_dim = ds.createDimension("time", n_times)
        row_dim = ds.createDimension("grid_row", n_rows)
        col_dim = ds.createDimension("grid_col", n_cols)
        location_dim = ds.createDimension("location", n_locations)
        string_dim = ds.createDimension("string_length", 64)

        # Create coordinate variables
        time_var = ds.createVariable("time", "i4", ("time",))
        row_var = ds.createVariable("grid_row", "i4", ("grid_row",))
        col_var = ds.createVariable("grid_col", "i4", ("grid_col",))

        time_var[:] = times
        row_var[:] = np.arange(row_min, row_max + 1)
        col_var[:] = np.arange(col_min, col_max + 1)

        time_var.long_name = "Time index"
        row_var.long_name = "Model grid row index"
        col_var.long_name = "Model grid column index"

        # Create location metadata
        location_ids = ds.createVariable("location_id", "i4", ("location",))
        location_names_var = ds.createVariable(
            "location_names", "S1", ("location", "string_length")
        )

        sorted_locs = sorted(location_names)
        for i, loc_name in enumerate(sorted_locs):
            location_ids[i] = i
            location_names_var[i] = np.array(list(loc_name.ljust(64)), dtype="S1")

        location_ids.long_name = "Location ID"
        location_names_var.long_name = "Location names (indexed by location ID)"

        # Global attributes
        ds.setncattr("title", "Gridded HMS Forecast - Incremental Excess")
        ds.setncattr("source", "RUN_Forecast.h5")
        ds.setncattr("created", datetime.now().isoformat())
        ds.setncattr("method", "Direct grid mapping (no interpolation)")
        ds.setncattr(
            "grid_coverage_percent",
            100 * np.sum(row_col_to_cellid_array >= 0) / row_col_to_cellid_array.size,
        )

        logger.info(f"Grid coverage: {ds.getncattr('grid_coverage_percent'):.1f}%")

        logger.info("\n" + "=" * 80)
        logger.info("STEP 5: Writing incremental excess data")
        logger.info("=" * 80)

        # Create main data variable
        ie_var = ds.createVariable(
            "incremental_excess",
            "f4",
            ("time", "grid_row", "grid_col", "location"),
            fill_value=np.nan,
            zlib=True,
            complevel=4,
        )
        ie_var.units = "mm or equivalent"
        ie_var.long_name = "Incremental Excess - mapped to grid"
        ie_var.description = "NaN where data not available"

        # Initialize with NaN
        ie_data = np.full(
            (n_times, n_rows, n_cols, n_locations), np.nan, dtype=np.float32
        )

        # Fill with data
        for loc_idx, loc_name in enumerate(sorted_locs):
            if loc_idx % 50 == 0:
                logger.info(
                    f"  Processing location {loc_idx + 1}/{n_locations}: {loc_name}"
                )

            inc_excess = all_data[loc_name]  # (time, cells)
            cell_mapping = location_grid_mapping[loc_name]

            for cell_id, data_idx in cell_mapping:
                # Find row, col from cell_id
                row_idx, col_idx = np.where(row_col_to_cellid_array == cell_id)
                if len(row_idx) > 0:
                    r, c = row_idx[0], col_idx[0]
                    ie_data[:, r, c, loc_idx] = inc_excess[:, data_idx]

        # Write to file
        ie_var[:] = ie_data

        logger.info(f"Successfully written incremental_excess variable")
        logger.info(f"Variable shape: {ie_var.shape}")

        ds.sync()
        logger.info(f"\n✓ NetCDF file created: {output_file}")
        logger.info(f"  File size: {Path(output_file).stat().st_size / 1e9:.2f} GB")

    finally:
        ds.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Fast gridded forecast NetCDF creation"
    )
    parser.add_argument(
        "--input",
        default="/home/ubuntu/pilot/hmsbox/data/RUN_Forecast.h5",
        help="Input HDF5 file",
    )
    parser.add_argument(
        "--output",
        default="/home/ubuntu/pilot/hmsbox/data/processed/forecast_grid_fast.nc",
        help="Output NetCDF file",
    )

    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    create_gridded_netcdf_fast(args.input, args.output)
