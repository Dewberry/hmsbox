"""Export HMS results to timeseries NetCDF grid using an inferred grid transform, typically SHG x Subbasins.


Stage 1:
    Optional. Infer a gridding strategy from the HMS discretization's final polygon
    staircase pattern.

    Write:
        A 1-band tif of the inferred grid with randomized cell groups for visual review.
        A fgb file of slivers found, as polygons. Another fgb of representative points.
        A report json file.

    Stage 1 output files are only for review and are not ingested by Stage 2.
    Stage 1 may be skipped if confident that the gridding plan will be sufficient.

Stage 2:
    Using the same gridding strategy as Stage 1, export a timeseries variable to NetCDF.
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
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin

logger = logging.getLogger("converter")

TURN_TOLERANCE_DEG = (
    1e-4  # allowed deviation of a vertex turn from 0, 90 or 270 degrees
)
STEP_TOLERANCE = 0.01  # allowed edge/vertex misfit, as a fraction of the step
MAX_SHORTER_FRACTION = (
    0.01  # outline edges allowed to be shorter than the most common length
)
RANDOM_SEED = 0
BLOCK_SIZE = 512
OVERVIEW_FACTORS = [2, 4, 8, 16]
CRS_WKT_SQL = (
    "SELECT srtext FROM spatial_ref_sys WHERE srid ="
    " (SELECT srid FROM geometry_columns WHERE f_table_name = 'discretization')"
)
TIME_CALENDAR = "proleptic_gregorian"
TIME_UNITS = "hours since 1970-01-01"
COMPRESSION_LEVEL = 6
INCH_TO_MM = 25.4
HMS_SOURCE_UNIT_NAMES = {"IN": "in", "MM": "mm"}
UNIT_CONVERSIONS_TO_MM = {"IN": INCH_TO_MM, "MM": 1.0}
HMS_RESULTS_VAR_METADATA = {
    "Incremental Excess": {
        "name": "PRECIPITATION",
        "description": "HEC-HMS results: excess precipitation (incremental)",
        "standard_name": "lwe_thickness_of_precipitation_amount",
        "cell_methods": "time: sum",
        "output_units": "get_from_source",
    }
}


def infer_grid_params(hms_disc_file: str, layer_name: str) -> dict:
    """Infer the grid strategy that produced the stair-stepped discretization polygons,
    e.g. SHG x delin resolution.

    ``hms_disc_file``: An HMS discretization file, e.g. a .sqlite (GDAL path, /vsi* ok).
    ``layer_name``: Subbasin outline layer, typically "subbasin2d".
    """
    lengths, turns, vertices = [], [], []
    for outline in gpd.read_file(hms_disc_file, layer=layer_name, columns=[]).geometry:
        for part in getattr(outline, "geoms", [outline]):
            for ring in [part.exterior, *part.interiors]:
                xy = list(ring.coords)  # closed: last point == first
                headings = []
                for (x1, y1), (x2, y2) in zip(xy[:-1], xy[1:]):
                    if (x1, y1) == (x2, y2):
                        continue  # degenerate: repeated vertex
                    lengths.append(math.hypot(x2 - x1, y2 - y1))
                    headings.append(math.degrees(math.atan2(y2 - y1, x2 - x1)))
                turns += [
                    (b - a) % 360 for a, b in zip(headings, headings[1:] + headings[:1])
                ]
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

    def __init__(self, hms_results_file: Path | str, hms_disc_file: str):
        """``hms_disc_file`` is a GDAL path, so /vsis3/, /vsicurl/ etc. work."""
        self.hms_results_file, self.hms_disc_file = (
            Path(hms_results_file),
            hms_disc_file,
        )
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
        outputs = tif, slivers_fgb, points_fgb, report_json = [
            output_dir / f"{stem}_{s}"
            for s in ("grid.tif", "slivers.fgb", "sliver_points.fgb", "report.json")
        ]
        for path in outputs:
            logger.info(f"Deleting if exists: {path}")
            path.unlink(missing_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        sections = {}
        try:
            grid_params, cell = self.grid()
            # Count pixels per cell ID. Ignore background ID 0 so counts align
            # with self.cells.
            # Zero counts mark cells with no rasterized pixels (slivers).
            sliver = np.bincount(cell.ravel(), minlength=len(self.cells) + 1)[1:] == 0
            sections = {
                "grid_params": grid_params,
                "grid": {
                    "w": cell.shape[1],
                    "h": cell.shape[0],
                    "pixels_in_domain": int((cell > 0).sum()),
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
            self.problems.append(f"Grid: {e}")
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
        metadata = HMS_RESULTS_VAR_METADATA.get(variable)
        if metadata is None:
            raise ValueError(
                f"Unsupported HMS results variable {variable!r}. available: {list(HMS_RESULTS_VAR_METADATA)}"
            )
        self.check_results(variable)
        grid_params, cell = self.grid()
        if min(cell.shape) < BLOCK_SIZE:
            raise ValueError(
                "Fixed (1, 512, 512) chunks require at least 512 rows and columns"
            )

        output_file = Path(output_file)
        if output_file.resolve() in {
            self.hms_results_file.resolve(),
            Path(self.hms_disc_file).resolve(),
        }:
            raise ValueError("Output must not overwrite an input file")
        temporary_path = None
        try:
            with h5py.File(self.hms_results_file, "r") as source:
                datasets, time_values, time_units, calendar, units = (
                    self._s2_read_results(source, variable)
                )
                try:
                    source_units = units.upper()
                    output_units = metadata["output_units"]
                    if output_units == "get_from_source":
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
                    values = np.full(
                        len(self.cells) + 1, data._FillValue, dtype="float32"
                    )
                    steps = len(time_values)
                    logger.info(
                        f"Writing {output_file!r}: {steps} timesteps, {w} x {h} pixels."
                    )
                    logger.info(f"Chunks: (time, y, x)={tuple(data.chunking())}")
                    logger.info(f"Filters: {data.filters()} Block size: {BLOCK_SIZE}")
                    for timestep in range(steps):
                        if (
                            (timestep + 1) % 5 == 0
                            or timestep == 0
                            or timestep == steps - 1
                        ):
                            logger.info(
                                f"Writing NetCDF timestep {timestep + 1}/{steps}"
                            )
                        for dataset, indices, missing in datasets:
                            row = dataset[timestep, :]
                            valid = np.isfinite(row) & (row != missing)
                            row_values = np.full(
                                row.shape, data._FillValue, dtype="float32"
                            )
                            row_values[valid] = row[valid] * unit_conversion
                            values[indices + 1] = row_values
                        for start in range(0, h, BLOCK_SIZE):
                            block = cell[start : start + BLOCK_SIZE]
                            data[timestep, start : start + BLOCK_SIZE, :] = values[
                                block
                            ]
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
        """Return inferred grid parameters and a int32 Boolean mask of it."""
        grid_params = infer_grid_params(self.hms_disc_file, "subbasin2d")
        step, x0, y0 = grid_params["step"], grid_params["x0"], grid_params["y0"]
        xmin, ymin, xmax, ymax = self.cells.total_bounds
        col0, col1 = np.floor((xmin - x0) / step), np.ceil((xmax - x0) / step)
        row0, row1 = np.floor((ymin - y0) / step), np.ceil((ymax - y0) / step)
        left, top = float(x0 + col0 * step), float(y0 + row1 * step)
        cell = rasterize(
            zip(self.cells.geometry, range(1, len(self.cells) + 1)),
            out_shape=(int(row1 - row0), int(col1 - col0)),
            transform=from_origin(left, top, step, step),
            fill=0,
            dtype="int32",
        )
        grid_params["geotransform"] = [left, step, 0.0, top, 0.0, -step]
        crs = self.cells.crs
        unit = crs.axis_info[0].unit_name
        step_m = step * crs.axis_info[0].unit_conversion_factor
        # edges = grid_params["edges"]
        # one_pixel = grid_params["one_step_fraction"]
        # vertex_error = grid_params["max_vertex_misfit"]
        logger.info(f"Grid Unit: {unit!r}, Grid CRS: {crs.name!r}")
        logger.info(
            f"Inferred Grid Resolution, Shape, Origin: ~{step:.6f} {unit!r} (~{step_m:.4f} m), ({cell.shape[1]}, {cell.shape[0]}), Origin: ~({left:.4f}, {top:.4f})"
        )
        return grid_params, cell

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
                    f"{len(wrong)} subbasins differ in (results, discretization) "
                    f"cell count for {variable!r}: {dict(list(wrong.items())[:5])}"
                )
        logger.info(
            f"Results match discretization: {len(disc)} subbasins, {len(self.cells)} cells"
        )

    def _s1_write_grid(self, path: Path, grid_params: dict, cell: np.ndarray) -> None:
        """Write rasterized discretization with randomization for visual review."""
        values = np.random.default_rng(RANDOM_SEED).integers(
            1, 256, size=len(self.cells) + 1, dtype=np.uint8
        )
        values[0] = 0
        h, w = cell.shape
        with rasterio.open(
            path, "w", driver="GTiff", width=w, height=h, count=1, dtype="uint8", nodata=0,
            crs=self.crs_wkt, transform=rasterio.Affine.from_gdal(*grid_params["geotransform"]),
            tiled=True, blockxsize=BLOCK_SIZE, blockysize=BLOCK_SIZE, compress="zstd",
        ) as dst:  # fmt: skip
            dst.write(values[cell], 1)
            dst.build_overviews(OVERVIEW_FACTORS, Resampling.nearest)

    def _s1_write_slivers(
        self, polygons_path: Path, points_path: Path, sliver: np.ndarray
    ) -> None:
        """Write two FlatGeobufs for review: sliver polygons and a point within each."""
        slivers = self.cells[sliver].rename_axis("ogc_fid").reset_index()
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
        """Read the HMS time axis, units, and datasets in discretization order."""

        def attr(dataset, name, default=""):
            value = np.asarray(dataset.attrs.get(name, default)).reshape(-1)[0]
            return value.decode() if isinstance(value, bytes) else str(value)

        subbasins = self.cells.groupby("subbasin", sort=False).indices
        first_dataset = source["results"][next(iter(subbasins))][variable]
        time_path = attr(first_dataset, "coordinates").split(":")[-1]
        times = source[time_path]
        time_values = times[:].reshape(-1)
        time_units = attr(times, "units")
        calendar = attr(times, "calendar", "standard").lower()
        netCDF4.num2date(time_values, time_units, calendar)
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
        """Write the HMS time axis and the discretization grid's coordinates and CRS."""
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
        left, step, _, top, _, _ = grid_params["geotransform"]
        axis_units = {
            "meter": "m",
            "metre": "m",
            "foot": "ft",
            "US survey foot": "US_survey_foot",
        }[self.cells.crs.axis_info[0].unit_name]
        for name, coordinates in (
            ("x", left + (np.arange(w) + 0.5) * step),
            ("y", top - (np.arange(h) + 0.5) * step),
        ):
            coordinate = target.createVariable(name, "float64", (name,))
            coordinate.standard_name = f"projection_{name}_coordinate"
            coordinate.long_name = f"{name} coordinate of projection"
            coordinate.units = axis_units
            coordinate.axis = name.upper()
            coordinate[:] = coordinates
        crs = target.createVariable("crs", "int32")
        crs.setncatts(self.cells.crs.to_cf())
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
            chunksizes=(1, BLOCK_SIZE, BLOCK_SIZE),
        )
        data.long_name = metadata["name"]
        data.units = output_units
        data.description = metadata["description"]
        data.standard_name = metadata["standard_name"]
        data.cell_methods = metadata["cell_methods"]
        data.grid_mapping = "crs"
        return data
