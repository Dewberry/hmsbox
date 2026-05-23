"""
HMS Forecast Analysis Script
Generates visualizations and analysis for HMS forecast results.

Data structure notes:
- MRMS QPE: time-based, dimension (time: 336, latitude: 350, longitude: 500)
- HRRR QPF: init_time + step based, dimensions (init_time: 1, step: 18, y: 132, x: 160)
- Lookback/Forecast Parquet: long format with timestamp, value, site_id, variable columns
"""

import json
import math
import os
import warnings
from io import BytesIO
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib.animation as animation
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import xarray as xr
from PIL import Image as PILImage

warnings.filterwarnings("ignore")

# NWS standard precipitation colormap (inches)
# Starts at 0.01 in — values below that are left transparent (no fill)
# Upper bounds extended to 30 in for multi-day cumulative totals
_NWS_PRECIP_BOUNDS = [
    0.01,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    1.5,
    2.0,
    2.5,
    3.0,
    4.0,
    5.0,
    6.0,
    8.0,
    10.0,
    15.0,
    20.0,
    30.0,
]
_NWS_PRECIP_COLORS = [
    "#04e9e7",
    "#019ff4",
    "#0300f4",  # cyan → blue
    "#02fd02",
    "#01c501",
    "#008e00",  # light → dark green
    "#fdf802",
    "#e5bc00",
    "#fd9500",  # yellow → orange
    "#fd0000",
    "#d40000",
    "#bc0000",  # light → dark red
    "#f800fd",
    "#9854c6",  # magenta, purple
    "#680068",
    "#4b0082",
    "#2d004b",  # dark purple tiers (10–30 in)
]
_NWS_CMAP = mcolors.ListedColormap(_NWS_PRECIP_COLORS)
_NWS_CMAP.set_under("none")  # transparent for values below the lowest bound
_NWS_CMAP.set_bad("none")  # transparent for NaN / masked values
_NWS_NORM = mcolors.BoundaryNorm(_NWS_PRECIP_BOUNDS, _NWS_CMAP.N)


# Configure matplotlib for non-interactive backend
plt.switch_backend("Agg")

# Paths - mounted in container or local
DATA_PATH = os.getenv("DATA_PATH", "/notebooks/data")
MODEL_PATH = os.getenv("MODEL_PATH", "/notebooks/model")
WORK_PATH = os.getenv("WORK_PATH", "/notebooks/work")
OUTPUT_PATH = WORK_PATH

FORCING_PATH = f"{DATA_PATH}/forcing"
RESULTS_PATH = f"{MODEL_PATH}/results"
OBS_PATH = os.getenv("OBS_PATH", f"{DATA_PATH}/observations")
GEOJSON_PATH = f"{MODEL_PATH}/basinStates/Junction.geojson"


def get_var_by_pattern(ds, patterns):
    """Find first variable matching any pattern."""
    for var in ds.data_vars:
        if any(pat.lower() in var.lower() for pat in patterns):
            return var
    return list(ds.data_vars)[0] if ds.data_vars else None


def _deg2tile(lat, lon, zoom):
    """Convert lat/lon to OSM tile coordinates."""
    n = 2**zoom
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return x, y


def _tile2deg(x, y, zoom):
    """Convert OSM tile corner to lat/lon."""
    n = 2**zoom
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def fetch_osm_basemap(lon_min, lon_max, lat_min, lat_max, zoom=8):
    """Fetch OpenStreetMap tiles and stitch into a basemap image array."""
    enable_basemap = os.getenv("ENABLE_OSM_BASEMAP", "1").strip().lower()
    if enable_basemap in {"0", "false", "no", "off"}:
        raise RuntimeError("OSM basemap disabled by ENABLE_OSM_BASEMAP")

    request_timeout = float(os.getenv("OSM_TILE_TIMEOUT_SEC", "2.5"))

    # NW corner -> smallest x and y tile
    x_min, y_min = _deg2tile(lat_max, lon_min, zoom)
    # SE corner -> largest x and y tile
    x_max, y_max = _deg2tile(lat_min, lon_max, zoom)
    n_x = x_max - x_min + 1
    n_y = y_max - y_min + 1
    tile_size = 256
    stitched = PILImage.new("RGB", (n_x * tile_size, n_y * tile_size))
    headers = {"User-Agent": "hmsbox/1.0 (educational use)"}
    for tx in range(x_min, x_max + 1):
        for ty in range(y_min, y_max + 1):
            url = f"https://tile.openstreetmap.org/{zoom}/{tx}/{ty}.png"
            try:
                r = requests.get(url, headers=headers, timeout=request_timeout)
                tile_img = PILImage.open(BytesIO(r.content)).convert("RGB")
                stitched.paste(
                    tile_img, ((tx - x_min) * tile_size, (ty - y_min) * tile_size)
                )
            except Exception:
                pass
    lat_top, lon_left = _tile2deg(x_min, y_min, zoom)
    lat_bottom, lon_right = _tile2deg(x_max + 1, y_max + 1, zoom)
    extent = [lon_left, lon_right, lat_bottom, lat_top]
    return np.array(stitched), extent


def _plot_cumulative_panel(ax, precip, lats, lons, times, title):
    """Render a single cumulative QPE panel onto ax (imshow, same style as HRRR)."""
    cumulative = np.cumsum(precip, axis=0)[-1]
    lon_min, lon_max = float(lons.min()), float(lons.max())
    lat_min, lat_max = float(lats.min()), float(lats.max())

    try:
        basemap_img, basemap_extent = fetch_osm_basemap(
            lon_min, lon_max, lat_min, lat_max, zoom=8
        )
        # Desaturate basemap so NWS colors pop
        gray = np.mean(basemap_img, axis=2, keepdims=True)
        faded = (0.4 * basemap_img + 0.6 * gray).astype(np.uint8)
        ax.imshow(faded, extent=basemap_extent, aspect="auto", zorder=0)
    except Exception:
        pass

    # Flip so north is up if lats are ascending (south→north)
    plot_data = cumulative[::-1] if lats[0] < lats[-1] else cumulative

    im = ax.imshow(
        plot_data,
        extent=[lon_min, lon_max, lat_min, lat_max],
        aspect="auto",
        cmap=_NWS_CMAP,
        norm=_NWS_NORM,
        alpha=0.25,
        zorder=1,
    )
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.grid(True, alpha=0.3, linestyle="--", zorder=3)
    ax.set_xlabel("Longitude", fontsize=10)
    ax.set_ylabel("Latitude", fontsize=10)
    ax.set_title(title, fontsize=12, fontweight="bold")
    return im


def plot_cumulative_forcing(mrms_file):
    """Plot cumulative MRMS QPE: two panels — full lookback and last 72 hours."""
    mrms_data = xr.open_dataset(mrms_file)

    precip_var = get_var_by_pattern(mrms_data, ["precip", "qpe", "qpe_in", "rate"])
    if precip_var is None:
        return None

    precip = mrms_data[precip_var].values  # (time, lat, lon)
    # Data is always in mm — convert to inches unconditionally
    precip = precip / 25.4
    times = mrms_data["time"].values
    lats = mrms_data["latitude"].values
    lons = mrms_data["longitude"].values

    # Last 72 hourly timesteps (or fewer if data is shorter)
    n_72h = min(72, len(times))
    precip_72h = precip[-n_72h:]
    times_72h = times[-n_72h:]

    def _fmt(t):
        try:
            return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(t)

    # Compute actual lookback duration from the timestamp range
    try:
        t0 = pd.Timestamp(times[0])
        t1 = pd.Timestamp(times[-1])
        total_days = (t1 - t0).total_seconds() / 86400
        if total_days >= 1:
            duration_str = f"{total_days:.0f} days"
        else:
            duration_str = f"{total_days * 24:.0f} hours"
    except Exception:
        duration_str = f"{len(times)} steps"

    fig, axes = plt.subplots(1, 2, figsize=(22, 9))

    im0 = _plot_cumulative_panel(
        axes[0],
        precip,
        lats,
        lons,
        times,
        f"Full Lookback ({duration_str})\n{_fmt(times[0])} → {_fmt(times[-1])}",
    )
    _plot_cumulative_panel(
        axes[1],
        precip_72h,
        lats,
        lons,
        times_72h,
        f"Last 72 Hours\n{_fmt(times_72h[0])} → {_fmt(times_72h[-1])}",
    )

    fig.colorbar(
        im0,
        ax=axes.tolist(),
        orientation="horizontal",
        location="bottom",
        label="Cumulative Precipitation (in)",
        ticks=_NWS_PRECIP_BOUNDS,
        boundaries=_NWS_PRECIP_BOUNDS,
        pad=0.08,
        aspect=50,
    )

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.30)
    output_file = f"{OUTPUT_PATH}/01_cumulative_forcing.png"
    plt.savefig(output_file, dpi=100, bbox_inches="tight")
    plt.close()

    return (
        float(np.cumsum(precip, axis=0)[-1].min()),
        float(np.cumsum(precip, axis=0)[-1].max()),
    )


def create_forecast_animation(hrrr_file):
    """Create animated forecast from HRRR QPF with OSM basemap."""
    # print("Loading HRRR data...")
    hrrr_data = xr.open_dataset(hrrr_file)

    precip_var = get_var_by_pattern(
        hrrr_data, ["precip", "qpf", "hourly_accum", "acum", "rate"]
    )

    if precip_var is None:
        # print("Warning: Could not find precipitation variable in HRRR data")
        return None

    # print(f"  Using variable: {precip_var}")

    # Get data - (init_time, step, y, x)
    hrrr_precip = hrrr_data[precip_var].values
    # Data is always in mm — convert to inches unconditionally
    hrrr_precip = hrrr_precip / 25.4

    # Resolve per-step valid times from whatever coords are available
    def _extract_times(ds, n):
        def _as_datetime_list(values, count):
            arr = np.asarray(values).reshape(-1)
            if arr.size == 0:
                return None
            parsed = pd.to_datetime(arr, errors="coerce")
            if hasattr(parsed, "isna") and not parsed.isna().all():
                out = [t for t in parsed.to_pydatetime().tolist() if pd.notna(t)]
                return out[:count] if out else None
            return None

        def _step_to_timedelta(step_val, units_hint):
            arr = np.asarray(step_val)
            if np.issubdtype(arr.dtype, np.timedelta64):
                return pd.to_timedelta(step_val)

            if np.issubdtype(arr.dtype, np.number):
                sval = float(step_val)
                if "min" in units_hint:
                    return pd.to_timedelta(sval, unit="m")
                if "sec" in units_hint:
                    return pd.to_timedelta(sval, unit="s")
                if "day" in units_hint:
                    return pd.to_timedelta(sval, unit="D")
                return pd.to_timedelta(sval, unit="h")

            td = pd.to_timedelta(step_val, errors="coerce")
            return None if pd.isna(td) else td

        # Prefer explicit valid_time when available (often 2D: init_time × step)
        if "valid_time" in ds:
            vt = _as_datetime_list(ds["valid_time"].values, n)
            if vt and len(vt) >= n:
                return vt[:n]

        # Some files expose direct per-step time coordinate
        for dim in ("time",):
            if dim in ds.coords:
                dt = _as_datetime_list(ds[dim].values, n)
                if dt and len(dt) >= n:
                    return dt[:n]

        # Build valid times from init/reference time + step offsets
        init = None
        for dim in ("init_time", "forecast_reference_time", "time"):
            if dim in ds.coords:
                cand = pd.to_datetime(
                    np.asarray(ds[dim].values).reshape(-1)[0], errors="coerce"
                )
                if pd.notna(cand):
                    init = pd.Timestamp(cand)
                    break

        if init is not None and "step" in ds.coords:
            step_vals = np.asarray(ds["step"].values).reshape(-1)[:n]
            units_hint = str(ds["step"].attrs.get("units", "")).lower()
            built = []
            for s in step_vals:
                td = _step_to_timedelta(s, units_hint)
                if td is None:
                    built = []
                    break
                built.append(init + td)
            if len(built) == len(step_vals) and len(built) >= n:
                return built[:n]

        return [f"Step {i + 1}" for i in range(n)]

    times_hrrr = _extract_times(
        hrrr_data,
        hrrr_precip.shape[1] if hrrr_precip.ndim == 4 else hrrr_precip.shape[0],
    )

    n_steps = hrrr_precip.shape[1] if hrrr_precip.ndim == 4 else hrrr_precip.shape[0]

    # Determine geographic extent from 2D lat/lon coords
    has_latlon = "latitude" in hrrr_data.coords and "longitude" in hrrr_data.coords
    if has_latlon:
        lats_2d = hrrr_data["latitude"].values
        lons_2d = hrrr_data["longitude"].values
        lon_min, lon_max = float(lons_2d.min()), float(lons_2d.max())
        lat_min, lat_max = float(lats_2d.min()), float(lats_2d.max())
        data_extent = [lon_min, lon_max, lat_min, lat_max]
    else:
        data_extent = None

    # Compute global value range across all frames for consistent colorbar
    all_data = hrrr_precip[0] if hrrr_precip.ndim == 4 else hrrr_precip
    vmin, vmax = float(np.nanmin(all_data)), float(np.nanmax(all_data))
    if vmax == vmin:
        vmax = vmin + 1

    fig, ax = plt.subplots(figsize=(10, 8))

    # Draw OSM basemap once (static background)
    if data_extent:
        # print("  Fetching OSM basemap...")
        try:
            basemap_img, basemap_extent = fetch_osm_basemap(
                lon_min, lon_max, lat_min, lat_max, zoom=8
            )
            gray = np.mean(basemap_img, axis=2, keepdims=True)
            faded = (0.4 * basemap_img + 0.6 * gray).astype(np.uint8)
            ax.imshow(faded, extent=basemap_extent, aspect="auto", zorder=0)
        except Exception as e:
            # print(f"  Warning: Could not fetch basemap: {e}")
            pass

    # Draw initial frame
    data0 = hrrr_precip[0, 0] if hrrr_precip.ndim == 4 else hrrr_precip[0]
    kwargs = dict(cmap=_NWS_CMAP, norm=_NWS_NORM, alpha=0.25, zorder=1)
    if data_extent:
        kwargs["extent"] = data_extent
        kwargs["aspect"] = "auto"
    im = ax.imshow(data0, **kwargs)

    cbar = fig.colorbar(
        im,
        ax=ax,
        label="Precip (in)",
        ticks=_NWS_PRECIP_BOUNDS,
        boundaries=_NWS_PRECIP_BOUNDS,
    )

    if data_extent:
        ax.set_xlim(lon_min, lon_max)
        ax.set_ylim(lat_min, lat_max)
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
    else:
        ax.set_xlabel("X")
        ax.set_ylabel("Y")

    ax.set_title("HRRR QPF", fontsize=12)

    def update_frame(frame):
        data = hrrr_precip[0, frame] if hrrr_precip.ndim == 4 else hrrr_precip[frame]
        im.set_data(data)
        try:
            time_str = pd.Timestamp(times_hrrr[frame]).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            time_str = str(times_hrrr[frame])
        ax.set_title(f"HRRR QPF  —  {time_str}", fontsize=12)
        return [im]

    anim = animation.FuncAnimation(
        fig, update_frame, frames=range(n_steps), interval=500, blit=False, repeat=True
    )

    anim_file = f"{OUTPUT_PATH}/hrrr_forecast_animation.gif"
    try:
        anim.save(anim_file, writer="pillow", fps=2)
        # print(f"Animation saved: {anim_file}")
    except Exception as e:
        # print(f"Warning: Could not save animation: {e}")
        pass
    finally:
        plt.close()

    return anim_file


def plot_hydrographs():
    """Plot hydrographs for all sites. Sites with both flow and stage get side-by-side panels."""
    lookback_df = pd.read_parquet(f"{RESULTS_PATH}/lookback.parquet")
    forecast_df = pd.read_parquet(f"{RESULTS_PATH}/forecast.parquet")

    lookback_df["timestamp"] = pd.to_datetime(lookback_df["timestamp"])
    forecast_df["timestamp"] = pd.to_datetime(forecast_df["timestamp"])

    # Coerce to float first so NaN replacement works regardless of stored dtype,
    # then mask any large-negative HMS sentinel (-999, -9999, -901, etc.)
    lookback_df["value"] = pd.to_numeric(lookback_df["value"], errors="coerce")
    forecast_df["value"] = pd.to_numeric(forecast_df["value"], errors="coerce")
    lookback_df["value"] = lookback_df["value"].where(
        lookback_df["value"] > -100, np.nan
    )
    forecast_df["value"] = forecast_df["value"].where(
        forecast_df["value"] > -100, np.nan
    )

    # Load gage metadata to determine variable type (Flow vs Stage/Elevation)
    gage_meta = {}  # site_id -> "Flow" | "Stage" | unknown
    gage_file = Path(OBS_PATH) / "gages.parquet"
    if gage_file.exists():
        try:
            gages_df = pd.read_parquet(gage_file)
            # Expect columns like: site_id, variable / gage_type / GageType / type
            type_col = next(
                (
                    c
                    for c in gages_df.columns
                    if c.lower() in ("gagetype", "gage_type", "variable", "type")
                ),
                None,
            )
            id_col = next(
                (
                    c
                    for c in gages_df.columns
                    if c.lower() in ("site_id", "siteid", "gage_id", "id", "name")
                ),
                None,
            )
            if type_col and id_col:
                for _, row in gages_df.drop_duplicates(subset=[id_col]).iterrows():
                    gage_meta[str(row[id_col])] = str(row[type_col])
        except Exception:
            pass

    def _gage_type(site):
        raw = gage_meta.get(site, "")
        if "stage" in raw.lower() or "elevation" in raw.lower():
            return "stage"
        return "flow"

    # Collect all sites that appear in lookback as observed (any variable containing OBSERVED)
    obs_vars = lookback_df[lookback_df["variable"].str.contains("OBSERVED", na=False)]
    observed_sites = sorted(obs_vars["site_id"].unique().tolist())
    forecast_sites = set(forecast_df["site_id"].unique())

    if len(observed_sites) == 0:
        return 0

    def _plot_site(
        ax, lookback_site, forecast_site_df, obs_var, model_vars, ylabel, is_stage
    ):
        """Render one hydrograph panel."""
        obs_data = lookback_site[lookback_site["variable"] == obs_var]
        if len(obs_data) > 0:
            ax.plot(
                obs_data["timestamp"],
                obs_data["value"],
                color="black",
                linewidth=2.0,
                label="Observed",
                zorder=3,
            )

        # Modeled lookback
        for var in model_vars:
            mod = lookback_site[lookback_site["variable"] == var]
            if len(mod) > 0:
                ax.plot(
                    mod["timestamp"],
                    mod["value"],
                    color="steelblue",
                    linewidth=1.8,
                    linestyle="--",
                    label="Lookback Modeled",
                    zorder=2,
                )
                break

        # Forecast
        if forecast_site_df is not None:
            for var in model_vars:
                fc = forecast_site_df[forecast_site_df["variable"] == var]
                if len(fc) > 0:
                    ax.plot(
                        fc["timestamp"],
                        fc["value"],
                        color="darkorange",
                        linewidth=1.8,
                        linestyle="-.",
                        label="Forecast",
                        zorder=2,
                    )
                    break

        ax.set_ylabel(ylabel, fontsize=9)
        ax.legend(loc="upper left", fontsize=8, framealpha=0.7)
        ax.grid(True, alpha=0.3)
        ax.tick_params(axis="x", labelsize=8, rotation=30)

        # For stage/elevation panels, fix y-axis to observed min/max ± 10 ft
        # so scale differences don't distort visual interpretation
        if is_stage and len(obs_data) > 0:
            valid = obs_data["value"].dropna()
            if len(valid) > 0:
                ax.set_ylim(valid.min() - 10, valid.max() + 10)

    # Build per-site row specs: (has_flow, has_stage)
    site_specs = []
    for site in observed_sites:
        lb = lookback_df[lookback_df["site_id"] == site]
        vars_present = set(lb["variable"].unique())
        has_flow = bool(vars_present & {"FLOW-OBSERVED"})
        has_stage = bool(vars_present & {"STAGE-OBSERVED", "ELEVATION-OBSERVED"})
        # If gage file says it's a stage gage but only FLOW-OBSERVED exists, treat all as stage label
        if not (has_flow or has_stage):
            has_flow = True  # fallback
        site_specs.append((site, has_flow, has_stage))

    n_sites = len(site_specs)
    # Number of subplot columns per row: 2 if both, else 1
    # Use a single-column layout but double up when both present
    max_cols = 2
    rows = []
    for site, has_flow, has_stage in site_specs:
        ncols = 2 if (has_flow and has_stage) else 1
        rows.append((site, has_flow, has_stage, ncols))

    fig_height = max(4, 3 * n_sites)
    fig, axes_grid = plt.subplots(
        n_sites,
        max_cols,
        figsize=(16, fig_height),
        squeeze=False,
    )

    fig.suptitle(
        "Hydrographs: Observed, Lookback Modeled, and Forecast",
        fontsize=14,
        fontweight="bold",
        y=1.002,
    )

    for row_idx, (site, has_flow, has_stage, ncols) in enumerate(rows):
        lb = lookback_df[lookback_df["site_id"] == site].sort_values("timestamp")
        fc = (
            forecast_df[forecast_df["site_id"] == site].sort_values("timestamp")
            if site in forecast_sites
            else None
        )

        gtype = _gage_type(site)

        col = 0
        if has_flow:
            ax = axes_grid[row_idx, col]
            flow_ylabel = "Flow (cfs)"
            _plot_site(
                ax,
                lb,
                fc,
                "FLOW-OBSERVED",
                ["FLOW-COMBINE", "FLOW"],
                flow_ylabel,
                is_stage=False,
            )
            ax.set_title(f"{site}  —  Flow", fontsize=10, fontweight="bold")
            col += 1

        if has_stage:
            ax = axes_grid[row_idx, col]
            stage_var = (
                "STAGE-OBSERVED"
                if "STAGE-OBSERVED" in lb["variable"].values
                else "ELEVATION-OBSERVED"
            )
            stage_ylabel = (
                "Elevation (ft)" if "ELEVATION" in stage_var else "Stage (ft)"
            )
            _plot_site(
                ax,
                lb,
                fc,
                stage_var,
                ["STAGE-COMBINE", "STAGE", "ELEVATION"],
                stage_ylabel,
                is_stage=True,
            )
            ax.set_title(
                f"{site}  —  {stage_ylabel.split()[0]}", fontsize=10, fontweight="bold"
            )
            col += 1

        if not has_flow and not has_stage:
            # Fallback: plot whatever OBSERVED variable exists
            obs_var = next(
                (v for v in lb["variable"].unique() if "OBSERVED" in v), None
            )
            if obs_var:
                ax = axes_grid[row_idx, 0]
                ylabel = "Stage (ft)" if gtype == "stage" else "Flow (cfs)"
                _plot_site(
                    ax,
                    lb,
                    fc,
                    obs_var,
                    ["FLOW-COMBINE", "FLOW", "STAGE"],
                    ylabel,
                    is_stage=(gtype == "stage"),
                )
                ax.set_title(f"{site}", fontsize=10, fontweight="bold")
                col = 1

        # Hide unused columns in this row
        for c in range(col, max_cols):
            axes_grid[row_idx, c].set_visible(False)

    plt.tight_layout()
    output_file = f"{OUTPUT_PATH}/02_hydrographs.png"
    plt.savefig(output_file, dpi=100, bbox_inches="tight")
    plt.close()

    return n_sites


def process_nash_sutcliffe():
    """Process Nash-Sutcliffe Efficiency statistics."""
    # print("Loading statistics...")
    stats_df = pd.read_parquet(f"{RESULTS_PATH}/stats.parquet")

    # print(f"  Total records: {len(stats_df)}")

    # Filter for all Observed Flow statistics
    _OBSERVED_FLOW_TYPES = [
        "Observed Flow Bias Ratio",
        "Observed Flow Coefficient of Determination",
        "Observed Flow Correlation Coefficient",
        "Observed Flow Modified Kling-Gupta",
        "Observed Flow Nash Sutcliffe",
        "Observed Flow Percent Bias",
        "Observed Flow RMSE Stdev",
    ]
    if "StatisticType" in stats_df.columns:
        nse_stats = stats_df[
            stats_df["StatisticType"].isin(_OBSERVED_FLOW_TYPES)
        ].copy()
    else:
        nse_stats = stats_df

    if len(nse_stats) == 0:
        nse_stats = stats_df.head(50)

    # Save to CSV
    output_file = f"{OUTPUT_PATH}/stats_nash_sutcliffe.csv"
    nse_stats.to_csv(output_file, index=False)
    # print(f"Stats saved: {output_file} ({len(nse_stats)} records)")

    return nse_stats


def create_junction_map():
    """Create interactive map of junctions with peak flows."""
    # print("Loading junction data...")
    gdf = gpd.read_file(GEOJSON_PATH)
    # print(f"  Junctions: {len(gdf)}")

    # Load forecast data and calculate peak flows
    forecast_df = pd.read_parquet(f"{RESULTS_PATH}/forecast.parquet")

    # Pivot to get flow by site_id, then calculate max
    forecast_pivot = forecast_df.pivot_table(
        index="site_id", values="value", aggfunc="max"
    )
    peak_flows = forecast_pivot.reset_index()
    peak_flows.columns = ["site_id", "peak_flow"]

    # print(f"  Peak flows calculated for {len(peak_flows)} sites")

    # Determine junction-site linkage column
    junction_name_col = None
    for col in ["name", "site_id", "ID"]:
        if col in gdf.columns:
            junction_name_col = col
            break

    if junction_name_col is None:
        possible_cols = [col for col in gdf.columns if col not in ["geometry"]]
        junction_name_col = possible_cols[0] if possible_cols else None

    # print(f"  Using junction column: {junction_name_col}")

    # Merge with flows
    if junction_name_col:
        gdf_with_flows = gdf.merge(
            peak_flows, left_on=junction_name_col, right_on="site_id", how="left"
        )
    else:
        gdf_with_flows = gdf.copy()

    # Filter for high flows
    if "peak_flow" in gdf_with_flows.columns:
        high_flow_sites = gdf_with_flows[gdf_with_flows["peak_flow"] > 50]
        # print(f"  High flow junctions (>50 cfs): {len(high_flow_sites)}")
    else:
        high_flow_sites = pd.DataFrame()

    # Create map
    bounds = gdf_with_flows.total_bounds
    center_lat = (bounds[1] + bounds[3]) / 2
    center_lon = (bounds[0] + bounds[2]) / 2

    m = folium.Map(
        location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap"
    )

    # Add markers
    for idx, row in gdf_with_flows.iterrows():
        coords = row["geometry"]

        if coords.is_empty:
            continue

        if coords.geom_type == "Point":
            lat, lon = coords.y, coords.x
        else:
            lat, lon = coords.centroid.y, coords.centroid.x

        peak_flow = row.get("peak_flow", 0)
        junction_name = (
            row.get(junction_name_col, f"Junction {idx}")
            if junction_name_col
            else f"Junction {idx}"
        )

        # Color coding by flow
        if pd.notna(peak_flow) and peak_flow > 0:
            if peak_flow > 50:
                color, size = "red", 10
            elif peak_flow > 25:
                color, size = "orange", 7
            else:
                color, size = "blue", 5
        else:
            color, size, peak_flow = "gray", 5, 0

        popup_text = f"<b>{junction_name}</b><br>Peak: {peak_flow:.1f} cfs"

        folium.CircleMarker(
            location=[lat, lon],
            radius=size,
            popup=popup_text,
            color=color,
            fill=True,
            fillColor=color,
            fillOpacity=0.7,
            weight=2,
        ).add_to(m)

    # Add legend
    legend_html = """
    <div style="position: fixed; bottom: 50px; right: 50px; width: 200px; height: 160px;
                background-color: white; border: 2px solid grey; z-index: 9999;
                font-size: 14px; padding: 10px">
        <p style="margin: 0;"><b>Peak Flow (cfs)</b></p>
        <p style="margin: 5px 0;"><span style="color: red;">●</span> > 50</p>
        <p style="margin: 5px 0;"><span style="color: orange;">●</span> 25–50</p>
        <p style="margin: 5px 0;"><span style="color: blue;">●</span> < 25</p>
        <p style="margin: 5px 0;"><span style="color: gray;">●</span> No data</p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # print("Junction map created")
    return m._repr_html_(), (
        len(high_flow_sites) if isinstance(high_flow_sites, pd.DataFrame) else 0
    )


def main():
    """Run all analyses."""
    # Create output directory
    Path(OUTPUT_PATH).mkdir(parents=True, exist_ok=True)

    for step, fn in [
        (
            "cumulative forcing",
            lambda: (
                plot_cumulative_forcing(f"{FORCING_PATH}/mrms_qpe.nc")
                if os.path.exists(f"{FORCING_PATH}/mrms_qpe.nc")
                else None
            ),
        ),
        (
            "HRRR animation",
            lambda: (
                create_forecast_animation(f"{FORCING_PATH}/hrrr_qpf.nc")
                if os.path.exists(f"{FORCING_PATH}/hrrr_qpf.nc")
                else None
            ),
        ),
        ("hydrographs", plot_hydrographs),
        ("nash-sutcliffe", process_nash_sutcliffe),
    ]:
        try:
            fn()
        except Exception as e:
            print(f"  WARNING: {step} failed: {e}")

    # print(f"Analysis complete. Output: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    exit(main())
