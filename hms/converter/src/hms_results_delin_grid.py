"""Export HMS results to timeseries NetCDF grid.

This tool has two stages.
Stage 1 is optional and writes review layers.
Stage 2 writes the NetCDF, and does not read the Stage 1 outputs, but does leverage many of the same algorithms as Stage 1.

Must use either --match-raster <path>, or --infer-raster, not both.

With --match-raster, use an existing raster to define the output grid's resolution and alignment. The output grid is sized to minimally cover the discretization polygons, while preserving pixel snapping. CRS must be equivalent to that of the discretization polygons. Filepath or GDAL /vsi*/ path.

With --infer-raster, infer grid alignment and resolution from the discretization polygons' staircase pattern (commonly SHG x Subbasins).

In either case, the output CRS is the verbatim CRS WKT of the discretization polygon layer.

Grid cell IDs are 1-based, with 0 reserved for pixels outside the domain.

Sampling approach:
    1. Sample the raw results (polygon) data at the center of each output pixel.

    2. If a pixel's center misses the polygons but part of the pixel touches 1 or more polygons,
    then assign the value from the polygon with the greatest overlap.

Stages details:
    Stage 1:
        Optional. Write:
            A 1-band tif of the output grid with randomized cell groups for visual review.
            A fgb file of slivers found, as polygons. Another fgb of representative points.
            A report json file.
        Stage 1 output files are only for review and are not ingested by Stage 2.
        Stage 1 may be skipped if confident that the gridding plan will be sufficient.

    Stage 2:
        Using the same gridding strategy as Stage 1, export a timeseries variable to NetCDF.
        --chunk-time defaults to 1, resulting in a 1-timestep temporal chunk.
        --chunk-px (pixels) defaults to 128 and results in a 128x128 spatial chunk. It must not exceed the smaller grid dimension.
"""

import json
import logging
import math
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import h5py
import netCDF4
import numpy as np
import pyogrio
import rasterio
import shapely
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin

logger = logging.getLogger("converter")

# Misc
RANDOM_SEED = 0
### For getting the verbatim CRS WKT from the discretization layer
CRS_WKT_SQL = (
    "SELECT srtext FROM spatial_ref_sys WHERE srid ="
    " (SELECT srid FROM geometry_columns WHERE f_table_name = 'discretization')"
)
### Unit conversions (values and names)
INCH_TO_MM = 25.4
UNIT_CONVERSIONS_TO_MM = {"IN": INCH_TO_MM, "MM": 1.0}
HMS_SOURCE_UNIT_NAMES = {"IN": "in", "MM": "mm"}

# NetCDF settings
### Overridable defaults
DEFAULT_CHUNK_TIME = 1
DEFAULT_CHUNK_PX = 128
### Hard-coded
COMPRESSION_LEVEL = 6
TIME_CALENDAR = "proleptic_gregorian"
TIME_UNITS = "hours since 1970-01-01"
NETCDF_AXIS_UNIT_INFO = {
    "meter": ("m", 1.0),
    "metre": ("m", 1.0),
    "foot": ("ft", 0.3048),
    "US survey foot": ("US_survey_foot", 1200 / 3937),
}

# NOTE:
#   The sentinel OUT_UNITS_RETAIN_FROM_SOURCE preserves source units (IN -> in, MM -> mm), it indicates that unit conversion should not occur.
#   If anything other than OUT_UNITS_RETAIN_FROM_SOURCE is provided, it should be a member of UNIT_CONVERSIONS_TO_MM.keys() and the appropriate conversions will then be applied.
OUT_UNITS_RETAIN_FROM_SOURCE = "retain_from_source"

# TODO:
#   This is the list of allowed HMS result variables that can be exported with this tool.
#   This should be exposed to the user so they can specify which variables to export, output units, and details of the relevant NetCDF metadata.
HMS_RESULTS_VAR_METADATA = {
    "Incremental Excess": {
        "name": "PRECIPITATION_EXCESS_INC",
        "description": "HEC-HMS results: excess precipitation (incremental)",
        "standard_name": "lwe_thickness_of_precipitation_amount",
        "cell_methods": "time: sum",
        "output_units": OUT_UNITS_RETAIN_FROM_SOURCE,
    },
}

# Grid inference parameters
### Allowed deviation of a vertex turn from 0, 90 or 270 degrees
TURN_TOLERANCE_DEG = 1e-4
### Allowed edge/vertex misfit, as a fraction of the step
STEP_TOLERANCE = 0.01
### Outline edges allowed to be shorter than the most common length
MAX_SHORTER_FRACTION = 0.01
### For scalability
EDGE_ANALYSIS_BATCH_SIZE = 4096

# Processing Details
PROCESSING_BLOCK_SIZE = 512
### For (Stage 1) review tif
OVERVIEW_FACTORS = [2, 4, 8, 16]


class GridInferenceError(Exception):
    """Grid inference failed before a raster lattice could be determined."""


def _netcdf_axis_unit(crs: CRS) -> str:
    axis = crs.axis_info[0]
    try:
        netcdf_unit, expected_factor = NETCDF_AXIS_UNIT_INFO[axis.unit_name]
    except KeyError:
        raise ValueError(f"Unsupported CRS axis unit {axis.unit_name!r}") from None
    if not math.isclose(axis.unit_conversion_factor, expected_factor, rel_tol=1e-12):
        raise ValueError(
            f"CRS axis unit {axis.unit_name!r} has conversion factor {axis.unit_conversion_factor} but expected: {expected_factor}"
        )
    return netcdf_unit


def infer_grid_params(hms_disc_file: str, layer_name: str) -> dict:
    """Only used with --infer-grid, not used with --match-raster. Infer the grid strategy that produced the stair-stepped discretization polygons, e.g. SHG x delin resolution.

    ``hms_disc_file``: An HMS discretization file, e.g. a .sqlite (GDAL path, /vsi* ok).
    ``layer_name``: Subbasin outline layer, typically "subbasin2d".
    """
    lengths, turns, vertices = [], [], []
    for outline in gpd.read_file(hms_disc_file, layer=layer_name, columns=[]).geometry:
        for part in getattr(outline, "geoms", [outline]):
            for ring in [part.exterior, *part.interiors]:
                xy = list(ring.coords)  # closed: last point == first
                headings = []
                # Pair consecutive coordinates, excluding the repeated closing point.
                for (x1, y1), (x2, y2) in zip(xy[:-1], xy[1:]):
                    if (x1, y1) == (x2, y2):
                        continue  # degenerate: repeated vertex
                    lengths.append(math.hypot(x2 - x1, y2 - y1))
                    headings.append(math.degrees(math.atan2(y2 - y1, x2 - x1)))
                # Include the turn from the final edge back to the first edge.
                turns += [
                    (b - a) % 360 for a, b in zip(headings, headings[1:] + headings[:1])
                ]
                # Exclude the repeated closing coordinate from the vertex list.
                vertices += xy[:-1]
    lengths, turns, vertices = np.array(lengths), np.array(turns), np.array(vertices)

    # Turns of 0 deg (straight) and 90/270 deg (corner) are expected.
    # Anything else is not a raster edge.
    bad_turns = (
        np.abs(turns[:, None] - [0, 90, 270, 360]).min(axis=1) > TURN_TOLERANCE_DEG
    )
    if bad_turns.any():
        raise ValueError(
            f"{bad_turns.sum()} vertices turn by other than 0, 90 or 270 degrees"
        )

    # 1 pixel is the most common edge length (within tolerance: STEP_TOLERANCE).
    s = np.sort(lengths)
    equal = np.searchsorted(s, s * (1 + STEP_TOLERANCE)) - np.searchsorted(
        s, s * (1 - STEP_TOLERANCE)
    )
    # Mode: most common length is likely the raster resolution.
    mode = float(s[equal.argmax()])
    shorter = float((lengths < mode * (1 - STEP_TOLERANCE)).mean())
    if shorter > MAX_SHORTER_FRACTION:
        raise ValueError(
            f"Most common outline edge length {mode} is not among the smallest: {shorter:.1%} of edges are shorter."
        )
    pixels = np.round(lengths / mode)
    step = float(lengths.sum() / pixels.sum())
    if np.abs(lengths - pixels * step).max() > STEP_TOLERANCE * step:
        raise ValueError(f"Outline edges are not whole multiples of {step}")

    offsets = (vertices - vertices[0]) / step
    misfit = float(np.abs(offsets - np.round(offsets)).max() * step)
    if misfit > STEP_TOLERANCE * step:
        raise ValueError(f"Outline vertices miss the inferred grid by up to {misfit}")
    return {
        "step": step,
        "x0": float(vertices[0, 0]),
        "y0": float(vertices[0, 1]),
        "edges": int(lengths.size),
        "one_step_fraction": float((pixels == 1).mean()),
        "shorter_than_step_fraction": shorter,
        "max_vertex_misfit": misfit,
    }


class HMSResultsDelinGrid:
    __doc__ = __doc__

    def __init__(
        self,
        hms_results_file: Path | str,
        hms_disc_file: str,
        match_raster: Path | str | None = None,
        chunk_time: int = DEFAULT_CHUNK_TIME,
        chunk_px: int = DEFAULT_CHUNK_PX,
    ):
        """``hms_disc_file`` is a GDAL path, so /vsis3/, /vsicurl/ etc. work."""
        self.hms_results_file, self.hms_disc_file = (
            Path(hms_results_file),
            hms_disc_file,
        )
        self.match_raster = match_raster
        self.chunk_time = chunk_time
        self.chunk_px = chunk_px
        if not self.hms_disc_file.endswith(".sqlite"):
            raise ValueError(
                f"Expected a .sqlite HMS discretization file, got {self.hms_disc_file}"
            )
        self.problems: list[str] = []
        self.warnings: list[str] = []
        # Feature id is ogc_fid. Within a subbasin, cells are in results column order.
        logger.info(f"Reading: {self.hms_disc_file!r} layer 'discretization'")
        self.cells = gpd.read_file(
            self.hms_disc_file,
            layer="discretization",
            columns=["subbasin"],
            fid_as_index=True,
        ).sort_index()
        self.cells.geometry = self.cells.force_2d()
        self.cells["results_column"] = self.cells.groupby("subbasin").cumcount()
        # Get raw CRS WKT this way (as stored).
        self.crs_wkt = pyogrio.read_dataframe(
            self.hms_disc_file, sql=CRS_WKT_SQL, read_geometry=False
        )["srtext"].iloc[0]

    def stage_1(self, output_dir: Path | str) -> dict:
        """Optional. Infer a gridding strategy from the HMS discretization's final
        polygon staircase pattern, and write a representative tif as well as sliver
        files and a report for review.

        Stage 1 output files are only for review and are not ingested by Stage 2.
        Stage 1 may be skipped if confident that the gridding plan will be sufficient.
        """
        output_dir = Path(output_dir)
        stem = f"hms_h5_{self.hms_results_file.stem}_delin"
        if self.match_raster is None:
            stem += "_inferred"
        else:
            stem += f"_direct_{Path(self.match_raster).name}"
        outputs = tif, slivers_fgb, points_fgb, report_json = [
            output_dir / f"{stem}_{s}"
            for s in ("grid.tif", "slivers.fgb", "slivers_points.fgb", "report.json")
        ]
        for path in outputs:
            logger.info(f"Deleting if exists: {path}")
            path.unlink(missing_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        sections = {}
        try:
            grid_params, cell = self.grid()
            # Grid cell IDs are 1-based, with 0 reserved for pixels outside the domain.
            counts = np.zeros(len(self.cells) + 1, dtype="int64")
            for start in range(0, cell.shape[0], PROCESSING_BLOCK_SIZE):
                block = cell[start : start + PROCESSING_BLOCK_SIZE]
                counts += np.bincount(block.ravel(), minlength=len(counts))
            # Slivers are identified as zero-counts (cells with no rasterized pixels).
            sliver = counts[1:] == 0
            sections = {
                "grid_params": grid_params,
                "grid": {
                    "w": cell.shape[1],
                    "h": cell.shape[0],
                    # Grid cell IDs are 1-based, with 0 reserved for pixels outside the domain.
                    "pixels_in_domain": int(counts[1:].sum()),
                },
                "slivers": {
                    "count": int(sliver.sum()),
                    "total_area": float(self.cells.area[sliver].sum()),
                },
            }
            if sliver.any():
                self.warnings.append(f"{sliver.sum()} slivers. See: {slivers_fgb.name}")
            self._s1_write_grid(tif, grid_params, cell)
            self._s1_write_slivers(slivers_fgb, points_fgb, sliver)
        except Exception as e:
            problem = f"Grid: {e}"
            if isinstance(e, GridInferenceError):
                problem += " Consider avoiding grid inference and provide an explicit --match-raster instead."
            self.problems.append(problem)
        report = self._s1_write_report(report_json, **sections)

        for w in self.warnings:
            logger.warning(w)
        written = ", ".join(p.name for p in outputs if p.exists())
        logger.info(
            f"Wrote {written} in {output_dir}. Review before proceeding with Stage 2."
        )
        if self.problems:
            raise ValueError(
                f"Stage 1 found {len(self.problems)} problem(s). See {report_json}: "
                + " | ".join(self.problems)
            )
        return report

    def stage_2(self, output_file: Path | str, variable: str) -> Path:
        """Using shared gridding strategy, export a timeseries variable to NetCDF."""
        output_file = Path(output_file)
        if output_file.resolve() in {
            self.hms_results_file.resolve(),
            Path(self.hms_disc_file).resolve(),
        }:
            raise ValueError("Output must not overwrite an input file.")
        if not isinstance(self.chunk_time, int) or self.chunk_time < 1:
            raise ValueError(
                f"Time chunk must be a positive integer but got {self.chunk_time}"
            )
        if not isinstance(self.chunk_px, int) or self.chunk_px < 1:
            raise ValueError(
                f"Spatial chunk must be a positive integer but got {self.chunk_px}"
            )
        metadata = HMS_RESULTS_VAR_METADATA.get(variable)
        if metadata is None:
            raise ValueError(
                f"Unsupported HMS results variable {variable!r}. Choose from: {list(HMS_RESULTS_VAR_METADATA)}"
            )

        self.check_results(variable)

        grid_params, cell = self.grid()
        if self.chunk_px > min(cell.shape):
            raise ValueError(
                f"Spatial chunk must not exceed the grid dimensions: chunk_px={self.chunk_px} vs shape={cell.shape}"
            )
        chunk_bytes = self.chunk_time * self.chunk_px**2 * np.dtype("float32").itemsize
        if chunk_bytes >= 2**32:
            raise ValueError("NetCDF chunk must be smaller than 4 GiB")

        temporary_path = None
        try:
            with h5py.File(self.hms_results_file, "r") as source:
                datasets, time_values, time_units, calendar, units = (
                    self._s2_read_results(source, variable)
                )
                if self.chunk_time > len(time_values):
                    raise ValueError(
                        "Time chunk must not exceed the number of timesteps"
                    )
                try:
                    source_units = units.upper()
                    output_units = metadata["output_units"]
                    if output_units == OUT_UNITS_RETAIN_FROM_SOURCE:
                        output_units = HMS_SOURCE_UNIT_NAMES[source_units]
                        unit_conversion = 1.0
                    else:
                        unit_conversion = UNIT_CONVERSIONS_TO_MM[source_units]
                except KeyError:
                    raise ValueError(
                        f"Unsupported units {units!r} for {variable!r}"
                    ) from None
                output_file.parent.mkdir(parents=True, exist_ok=True)
                temporary_file = tempfile.NamedTemporaryFile(
                    prefix=f".{output_file.name}.",
                    suffix=".tmp",
                    dir=output_file.parent,
                    delete=False,
                )
                temporary_path = Path(temporary_file.name)
                temporary_file.close()
                with netCDF4.Dataset(temporary_path, "w", format="NETCDF4") as target:
                    self._s2_write_coordinates(
                        target,
                        grid_params,
                        cell.shape,
                        time_values,
                        time_units,
                        calendar,
                    )
                    data = self._s2_create_variable(target, metadata, output_units)
                    output_name = data.name
                    h, w = cell.shape
                    steps = len(time_values)
                    logger.info(
                        f"Writing {output_file!r}: {steps} timesteps, {w} x {h} pixels."
                    )
                    logger.info(f"Chunks: (time, y, x)={tuple(data.chunking())}")
                    logger.info(f"Filters: {data.filters()}")
                    # Grid cell IDs are 1-based, with 0 reserved for pixels outside the domain.
                    values = np.empty(len(self.cells) + 1, dtype="float32")
                    for timestep in range(steps):
                        self._s2_populate_timestep_values(
                            timestep,
                            steps,
                            datasets,
                            values,
                            data._FillValue,
                            unit_conversion,
                        )
                        data[timestep, :, :] = values[cell]
                    logger.info("Finalizing NetCDF file")
            assert temporary_path is not None
            temporary_path.chmod(0o644)
            temporary_path.replace(output_file)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
        logger.info(
            f"Wrote HMS results variable {variable!r} to {output_file!r} as {output_name!r}"
        )
        return output_file

    def grid(self) -> tuple[dict, np.ndarray]:
        """Return grid parameters and polygon lookup IDs (0 is outside the domain)."""
        if len(self.cells) > np.iinfo("uint32").max:
            raise ValueError("Polygon count exceeds uint32 lookup capacity")
        cell_dtype = "uint16" if len(self.cells) <= np.iinfo("uint16").max else "uint32"
        xmin, ymin, xmax, ymax = self.cells.total_bounds

        if self.match_raster is not None:
            logger.info(f"Applying match_raster: {self.match_raster!r}")
            with rasterio.open(self.match_raster) as snap:
                if snap.crs is None:
                    raise ValueError("Match raster has no CRS")
                if not CRS.from_wkt(snap.crs.to_wkt()).equals(self.cells.crs):
                    raise ValueError("Match raster CRS must equal discretization CRS")
                transform = snap.transform
                if (
                    transform.b != 0  # rotation
                    or transform.d != 0  # rotation
                    or transform.a <= 0  # x-resolution
                    or transform.e >= 0  # y-resolution (negative for north-up)
                ):
                    raise ValueError(
                        f"Match raster must be north-up with no rotation, but got transform: {transform}"
                    )

            # Apply transform of provided match grid to minimally cover discretizatio polygons
            col0, row0 = ~transform * (xmin, ymax)
            col1, row1 = ~transform * (xmax, ymin)
            col0, row0 = math.floor(col0), math.floor(row0)
            col1, row1 = math.ceil(col1), math.ceil(row1)
            transform *= rasterio.Affine.translation(col0, row0)
            shape = (row1 - row0, col1 - col0)
            grid_params = {"grid_source": "direct"}

        else:
            logger.info("No match_raster provided. Inferring grid parameters.")
            try:
                grid_params = infer_grid_params(self.hms_disc_file, "subbasin2d")
            except Exception as error:
                raise GridInferenceError(str(error)) from error
            grid_params["grid_source"] = "inferred"

            # Snap polygon bounds outward to whole cells of the inferred grid.
            # Apply transform of inferred grid to minimally cover discretizatio polygons
            step, x0, y0 = grid_params["step"], grid_params["x0"], grid_params["y0"]
            col0, col1 = np.floor((xmin - x0) / step), np.ceil((xmax - x0) / step)
            row0, row1 = np.floor((ymin - y0) / step), np.ceil((ymax - y0) / step)
            left, top = float(x0 + col0 * step), float(y0 + row1 * step)
            transform = from_origin(left, top, step, step)
            shape = (int(row1 - row0), int(col1 - col0))

        # Ensure dimensions are positive and the lookup array fits addressable memory.
        cell_bytes = np.dtype(cell_dtype).itemsize
        if min(shape) <= 0 or shape[0] * shape[1] > np.iinfo(np.intp).max // cell_bytes:
            raise ValueError("Grid dimensions exceed addressable array size")
        raster_buffer_bytes = PROCESSING_BLOCK_SIZE * shape[1] * cell_bytes
        with rasterio.Env(GDAL_CACHEMAX=raster_buffer_bytes):
            cell = rasterize(
                # Grid cell IDs are 1-based, with 0 reserved for pixels outside the domain.
                zip(self.cells.geometry, range(1, len(self.cells) + 1)),
                out_shape=shape,
                transform=transform,
                fill=0,
                dtype=cell_dtype,
            )

        self._fill_edge_cells(cell, transform)

        grid_params["match_raster"] = (
            str(self.match_raster) if self.match_raster is not None else None
        )
        grid_params["geotransform"] = list(transform.to_gdal())
        crs = self.cells.crs
        unit = crs.axis_info[0].unit_name
        # edges = grid_params["edges"]
        # one_pixel = grid_params["one_step_fraction"]
        # vertex_error = grid_params["max_vertex_misfit"]
        logger.info(f"Grid Unit: {unit!r}, Grid CRS: {crs.name!r}")
        logger.info(
            f"Grid ({grid_params['grid_source']}): {cell.shape[1]} x {cell.shape[0]} pixels, resolution ({transform.a:.6f}, {-transform.e:.6f}) {unit}, origin ({transform.c:.4f}, {transform.f:.4f})"
        )
        return grid_params, cell

    def _fill_edge_cells(self, cell: np.ndarray, transform: rasterio.Affine) -> None:
        """NOTE: updates ``cell`` in-place. Fill center-miss pixels with the largest-overlap polygon's 1-based ID.

        Process EDGE_ANALYSIS_BATCH_SIZE pixels at a time to limit intersection memory.
        Ignore zero-area contacts and break equal-area ties by feature order.
        """
        # Mark every pixel touched by a polygon, **even those whose center does not land in one**
        touched = rasterize(
            ((geometry, 1) for geometry in self.cells.geometry),
            out_shape=cell.shape,
            transform=transform,
            all_touched=True,
            dtype="uint8",
        )
        # Collect pixels whose centers miss, in row blocks to limit memory footprint.
        row_parts, col_parts = [], []
        for start in range(0, cell.shape[0], PROCESSING_BLOCK_SIZE):
            block = cell[start : start + PROCESSING_BLOCK_SIZE]
            touched_block = touched[start : start + PROCESSING_BLOCK_SIZE]
            rows, cols = np.nonzero((block == 0) & (touched_block != 0))
            if rows.size:
                row_parts.append(rows + start)
                col_parts.append(cols)

        # If none of the center-missing pixels need special treatment (none of them touch the polygons at all), then return early.
        if not row_parts:
            return

        # Handle pixels that touch polygons but whose centers do not.

        # Merge candidate coordinates.
        edge_rows = np.concatenate(row_parts)
        edge_cols = np.concatenate(col_parts)
        del touched, touched_block, row_parts, col_parts
        count_filled = 0
        geometries = self.cells.geometry.array
        spatial_index = self.cells.sindex

        # Resolve candidate pixels in bounded batches using exact polygon overlap.
        for start in range(0, len(edge_rows), EDGE_ANALYSIS_BATCH_SIZE):
            # Select the candidate pixel rows and columns for this batch.
            batch_rows = edge_rows[start : start + EDGE_ANALYSIS_BATCH_SIZE]
            batch_cols = edge_cols[start : start + EDGE_ANALYSIS_BATCH_SIZE]
            # Convert each candidate's row and column to its upper-left corner.
            left = transform.c + batch_cols * transform.a
            top = transform.f + batch_rows * transform.e
            # Build one polygon for each candidate pixel.
            pixels = shapely.box(left, top + transform.e, left + transform.a, top)

            # Each pair identifies a batch pixel and an intersecting polygon.
            pixel_indices, polygon_indices = spatial_index.query(
                pixels, predicate="intersects"
            )
            # Measure each candidate pixel's positive-area intersection with polygons.
            overlap_areas = shapely.area(
                shapely.intersection(pixels[pixel_indices], geometries[polygon_indices])
            )
            has_area = overlap_areas > 0
            pixel_indices = pixel_indices[has_area]
            polygon_indices = polygon_indices[has_area]
            overlap_areas = overlap_areas[has_area]

            # Sort by descending overlap area, then tie-break with polygon feature ID.
            ranked_pairs = np.lexsort((polygon_indices, -overlap_areas, pixel_indices))
            ranked_pixels = pixel_indices[ranked_pairs]

            # Keep each pixel's first ranked overlap (prioritized by area-of-overlap, then tie-break (for determinism) with arbitrary index.
            filled_pixels, first_pair = np.unique(ranked_pixels, return_index=True)
            # Map each winning overlap back to its polygon index.
            winning_polygons = polygon_indices[ranked_pairs[first_pair]]
            # Grid cell IDs are 1-based, with 0 reserved for pixels outside the domain.
            cell[batch_rows[filled_pixels], batch_cols[filled_pixels]] = (
                winning_polygons + 1
            )
            count_filled += len(filled_pixels)
        logger.info(
            f"Filled {count_filled} pixels whose centers did not intersect any polygon."
        )

    def check_results(self, variable: str) -> None:
        """Raise if any expected result's per-subbasin cell counts differ."""
        disc = self.cells["subbasin"].value_counts().to_dict()
        with h5py.File(self.hms_results_file, "r") as f:
            logger.info(
                f"Checking HMS results file {self.hms_results_file!r} for {variable!r}"
            )
            res = {}
            for name, grp in f["results"].items():
                if variable not in grp:
                    raise ValueError(
                        f"Var {variable!r} missing in {name}, available: {list(grp)}"
                    )
                if grp[variable].ndim != 2:
                    raise ValueError(f"Expected (time, cells) for {name}/{variable}")
                res[name] = grp[variable].shape[1]
            wrong = {
                subbasin: (res.get(subbasin), disc.get(subbasin))
                for subbasin in sorted(res.keys() | disc.keys())
                if res.get(subbasin) != disc.get(subbasin)
            }
            if wrong:
                raise ValueError(
                    f"{len(wrong)} subbasins differ in (results, discretization) cell count for {variable!r}: {dict(list(wrong.items())[:5])}"
                )
        logger.info(
            f"Results match discretization: {len(disc)} subbasins, {len(self.cells)} cells"
        )

    def _s1_write_grid(self, path: Path, grid_params: dict, cell: np.ndarray) -> None:
        """Write rasterized discretization with randomization for visual review."""
        # Grid cell IDs are 1-based, with 0 reserved for pixels outside the domain.
        values = np.random.default_rng(RANDOM_SEED).integers(
            1, 256, size=len(self.cells) + 1, dtype=np.uint8
        )
        values[0] = 0
        h, w = cell.shape
        with rasterio.open(
            path, "w", driver="GTiff", width=w, height=h, count=1, dtype="uint8", nodata=0,
            crs=self.crs_wkt, transform=rasterio.Affine.from_gdal(*grid_params["geotransform"]),
            tiled=True, blockxsize=PROCESSING_BLOCK_SIZE, blockysize=PROCESSING_BLOCK_SIZE, compress="zstd",
        ) as dst:  # fmt: skip
            for start in range(0, h, PROCESSING_BLOCK_SIZE):
                block = cell[start : start + PROCESSING_BLOCK_SIZE]
                window = rasterio.windows.Window(0, start, w, block.shape[0])
                dst.write(values[block], 1, window=window)
            dst.build_overviews(OVERVIEW_FACTORS, Resampling.nearest)

    def _s1_write_slivers(
        self, polygons_path: Path, points_path: Path, sliver: np.ndarray
    ) -> None:
        """Write two FlatGeobufs for review: sliver polygons and a point within each."""
        slivers = self.cells[sliver].rename_axis("ogc_fid").reset_index()
        # Convert zero-based positions to 1-based grid cell IDs.
        slivers.insert(0, "cell", np.flatnonzero(sliver) + 1)
        slivers["area"] = slivers.area
        slivers.to_file(polygons_path, driver="FlatGeobuf")
        slivers.set_geometry(slivers.representative_point()).to_file(
            points_path, driver="FlatGeobuf"
        )

    def _s1_write_report(self, path: Path, **sections) -> dict:
        """Write a report of the stage 1 results for review."""
        report = {
            "stage": 1,
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "hms_results_file": str(self.hms_results_file.absolute()),
            "hms_disc_file": self.hms_disc_file,
            "cells": len(self.cells),
            "crs_wkt": self.crs_wkt,
            **sections,
            "warnings": self.warnings,
            "problems": self.problems,
        }
        path.write_text(json.dumps(report, indent=2))
        return report

    def _s2_read_results(self, source: h5py.File, variable: str) -> tuple:
        """Validate and align results without mutating exporter state."""

        def attr(dataset, name, default=""):
            """Return the first attribute value as text, or the text default."""
            value = np.asarray(dataset.attrs.get(name, default)).reshape(-1)[0]
            return value.decode() if isinstance(value, bytes) else str(value)

        subbasins = self.cells.groupby("subbasin", sort=False).indices
        first_dataset = source["results"][next(iter(subbasins))][variable]
        time_path = attr(first_dataset, "coordinates").split(":")[-1]
        times = source[time_path]
        time_values = times[:].reshape(-1)
        time_units = attr(times, "units")
        calendar = attr(times, "calendar", "standard").lower()
        units = attr(first_dataset, "units")
        datasets = []
        for subbasin, indices in subbasins.items():
            dataset = source["results"][subbasin][variable]
            if (
                dataset.shape[0] != len(time_values)
                or attr(dataset, "coordinates").split(":")[-1] != time_path
                or attr(dataset, "units") != units
            ):
                raise ValueError(
                    f"Inconsistent time axis or units for {subbasin}/{variable}"
                )
            missing = float(attr(dataset, "missing_value", "nan"))
            datasets.append((dataset, indices, missing))
        return datasets, time_values, time_units, calendar, units

    def _s2_write_coordinates(
        self,
        target: netCDF4.Dataset,
        grid_params: dict,
        shape: tuple[int, int],
        time_values: np.ndarray,
        time_units: str,
        calendar: str,
    ) -> None:
        """Write explicit CF-1.9 time, coordinate, and grid-mapping metadata.

        Direct netCDF4 writes preserve the grid's exact pixel centers and CRS WKT.
        Alternative writers like xarray or GDAL still require explicit encoding
        and CF/GDAL attributes to preserve chunking and georeferencing details.
        https://cfconventions.org
        https://gdal.org/en/stable/drivers/raster/netcdf.html
        """
        target.Conventions = "CF-1.9"
        h, w = shape
        for name, size in (("time", len(time_values)), ("y", h), ("x", w)):
            target.createDimension(name, size)
        dates = netCDF4.num2date(time_values, time_units, calendar)
        epoch_hours = netCDF4.date2num(dates, TIME_UNITS, TIME_CALENDAR)
        time = target.createVariable("time", "float64", ("time",))
        time.setncatts(
            {
                "standard_name": "time",
                "long_name": "time",
                "axis": "T",
                "units": TIME_UNITS,
                "calendar": TIME_CALENDAR,
            }
        )
        time[:] = epoch_hours
        if len(epoch_hours) > 1:
            intervals = np.diff(epoch_hours)
            if np.any(intervals <= 0):
                raise ValueError("HMS result times must be strictly increasing")
            # Pair each end with the prior time. Extrapolate the first start.
            starts = np.concatenate(([epoch_hours[0] - intervals[0]], epoch_hours[:-1]))
            target.createDimension("nv", 2)
            time.bounds = "time_bnds"
            time_bounds = target.createVariable("time_bnds", "float64", ("time", "nv"))
            time_bounds.setncatts(
                {
                    "long_name": "time bounds",
                    "units": TIME_UNITS,
                    "calendar": TIME_CALENDAR,
                }
            )
            time_bounds[:] = np.column_stack((starts, epoch_hours))
        left, x_step, _, top, _, y_step = grid_params["geotransform"]
        axis_units = _netcdf_axis_unit(self.cells.crs)

        # Coordinates are pixel centers derived directly from the grid transform.
        for name, coordinates in (
            ("x", left + (np.arange(w) + 0.5) * x_step),
            ("y", top + (np.arange(h) + 0.5) * y_step),
        ):
            coordinate = target.createVariable(name, "float64", (name,))
            coordinate.standard_name = f"projection_{name}_coordinate"
            coordinate.long_name = f"{name} coordinate of projection"
            coordinate.units = axis_units
            coordinate.axis = name.upper()
            coordinate[:] = coordinates
        crs = target.createVariable("crs", "int32")
        crs.setncatts(CRS.from_wkt(self.crs_wkt).to_cf())
        crs.crs_wkt = self.crs_wkt
        crs.spatial_ref = self.crs_wkt
        crs.GeoTransform = " ".join(map(str, grid_params["geotransform"]))

    def _s2_create_variable(
        self, target: netCDF4.Dataset, metadata: dict, output_units: str
    ) -> netCDF4.Variable:
        """Create a float32 HMS variable with the output CRS and units."""
        data = target.createVariable(
            metadata["name"],
            "float32",
            ("time", "y", "x"),
            fill_value=netCDF4.default_fillvals["f4"],
            zlib=True,
            complevel=COMPRESSION_LEVEL,
            chunksizes=(self.chunk_time, self.chunk_px, self.chunk_px),
        )
        data.long_name = metadata["name"]
        data.units = output_units
        data.description = metadata["description"]
        data.standard_name = metadata["standard_name"]
        data.cell_methods = metadata["cell_methods"]
        data.grid_mapping = "crs"
        return data

    def _s2_populate_timestep_values(
        self,
        timestep: int,
        steps: int,
        datasets: list[tuple[h5py.Dataset, np.ndarray, float]],
        values: np.ndarray,
        fill_value: float,
        unit_conversion: float,
    ) -> None:
        """NOTE: updates ``cell`` in-place. Map one timestep's valid results into the reusable cell-value buffer."""
        if (timestep + 1) % 5 == 0 or timestep == 0 or timestep == steps - 1:
            logger.info(f"Writing NetCDF timestep {timestep + 1}/{steps}")
        values.fill(fill_value)
        for dataset, indices, missing in datasets:
            row = dataset[timestep, :]
            valid = np.isfinite(row)
            # Exclude HMS missing-value sentinels from the finite values.
            valid &= row != missing
            # Grid cell IDs are 1-based, with 0 reserved for pixels outside the domain.
            valid_indices = indices[valid] + 1

            ### If unit conversion requires float64 precision
            # row_values = np.full(row.shape, fill_value, dtype="float32")
            # with np.errstate(over="raise", invalid="raise"):
            #     converted = row[valid].astype("float64") * unit_conversion
            # if np.any(np.abs(converted) > np.finfo("float32").max):
            #     raise ValueError("HMS result values exceed float32 range")
            # row_values[valid] = converted

            if unit_conversion != 1:
                with np.errstate(over="raise", invalid="raise"):
                    values[valid_indices] = row[valid] * unit_conversion
            else:
                values[valid_indices] = row[valid]
