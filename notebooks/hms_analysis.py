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
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import xarray as xr
from PIL import Image as PILImage

warnings.filterwarnings("ignore")


# Configure matplotlib for non-interactive backend
plt.switch_backend("Agg")

# Paths - mounted in container or local
DATA_PATH = os.getenv("DATA_PATH", "/notebooks/data")
MODEL_PATH = os.getenv("MODEL_PATH", "/notebooks/model")
WORK_PATH = os.getenv("WORK_PATH", "/notebooks/work")
OUTPUT_PATH = WORK_PATH

FORCING_PATH = f"{DATA_PATH}/forcing"
RESULTS_PATH = f"{MODEL_PATH}/results"
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
                r = requests.get(url, headers=headers, timeout=10)
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


def plot_cumulative_forcing(mrms_file):
    """Plot cumulative MRMS QPE forcing with OSM basemap."""
    # print("Loading MRMS data...")
    mrms_data = xr.open_dataset(mrms_file)

    precip_var = get_var_by_pattern(mrms_data, ["precip", "qpe", "qpe_in", "rate"])

    if precip_var is None:
        # print("Warning: Could not find precipitation variable in MRMS data")
        return None

    # print(f"  Using variable: {precip_var}")

    # Get precipitation data - (time, latitude, longitude)
    precip = mrms_data[precip_var].values
    times = mrms_data["time"].values
    lats = mrms_data["latitude"].values
    lons = mrms_data["longitude"].values

    # Calculate cumulative sum along time dimension
    cumulative_precip = np.cumsum(precip, axis=0)
    final_cumulative = cumulative_precip[-1]  # Last time step

    lon_min, lon_max = float(lons.min()), float(lons.max())
    lat_min, lat_max = float(lats.min()), float(lats.max())

    fig, ax = plt.subplots(figsize=(14, 10))

    # OSM basemap
    # print("  Fetching OSM basemap...")
    try:
        basemap_img, basemap_extent = fetch_osm_basemap(
            lon_min, lon_max, lat_min, lat_max, zoom=8
        )
        ax.imshow(basemap_img, extent=basemap_extent, aspect="auto", zorder=0)
    except Exception as e:
        # print(f"  Warning: Could not fetch basemap: {e}")
        pass

    # Plot cumulative precipitation on top
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    contourf = ax.contourf(
        lon_grid,
        lat_grid,
        final_cumulative,
        levels=20,
        cmap="Blues",
        alpha=0.65,
        zorder=1,
    )
    contour = ax.contour(
        lon_grid,
        lat_grid,
        final_cumulative,
        levels=10,
        colors="navy",
        alpha=0.4,
        linewidths=0.5,
        zorder=2,
    )
    ax.clabel(contour, inline=True, fontsize=8)

    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.grid(True, alpha=0.3, linestyle="--", zorder=3)
    ax.set_xlabel("Longitude", fontsize=12)
    ax.set_ylabel("Latitude", fontsize=12)

    plt.colorbar(contourf, ax=ax, label="Cumulative Precipitation (inches)")

    try:
        start_time = pd.Timestamp(times[0]).strftime("%Y-%m-%d %H:%M")
        end_time = pd.Timestamp(times[-1]).strftime("%Y-%m-%d %H:%M")
        title = f"Cumulative MRMS QPE\n{start_time} to {end_time}"
    except Exception:
        title = "Cumulative MRMS QPE"

    ax.set_title(title, fontsize=14, fontweight="bold")

    plt.tight_layout()
    output_file = f"{OUTPUT_PATH}/01_cumulative_forcing.png"
    plt.savefig(output_file, dpi=100, bbox_inches="tight")
    plt.close()

    # print(f"Cumulative forcing saved: {output_file}")
    return (float(final_cumulative.min()), float(final_cumulative.max()))


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

    if "valid_time" in hrrr_data.coords:
        times_hrrr = hrrr_data["valid_time"].values
    else:
        times_hrrr = [f"Step {i}" for i in range(hrrr_precip.shape[1])]

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
            ax.imshow(basemap_img, extent=basemap_extent, aspect="auto", zorder=0)
        except Exception as e:
            # print(f"  Warning: Could not fetch basemap: {e}")
            pass
        pass

    # Draw initial frame
    data0 = hrrr_precip[0, 0] if hrrr_precip.ndim == 4 else hrrr_precip[0]
    kwargs = dict(cmap="YlGnBu", vmin=vmin, vmax=vmax, alpha=0.7, zorder=1)
    if data_extent:
        kwargs["extent"] = data_extent
        kwargs["aspect"] = "auto"
    im = ax.imshow(data0, **kwargs)

    cbar = fig.colorbar(im, ax=ax, label="Precip (inches)")

    if data_extent:
        ax.set_xlim(lon_min, lon_max)
        ax.set_ylim(lat_min, lat_max)
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
    else:
        ax.set_xlabel("X")
        ax.set_ylabel("Y")

    title = ax.set_title("")

    def update_frame(frame):
        data = hrrr_precip[0, frame] if hrrr_precip.ndim == 4 else hrrr_precip[frame]
        im.set_data(data)
        try:
            time_str = pd.Timestamp(times_hrrr[frame]).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            time_str = str(times_hrrr[frame])
        title.set_text(f"HRRR QPF - {time_str}")
        return [im, title]

    anim = animation.FuncAnimation(
        fig, update_frame, frames=range(n_steps), interval=500, blit=True, repeat=True
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
    """Plot hydrographs from long-format parquet files with observed data."""
    # print("Loading hydrograph data...")
    lookback_df = pd.read_parquet(f"{RESULTS_PATH}/lookback.parquet")
    forecast_df = pd.read_parquet(f"{RESULTS_PATH}/forecast.parquet")

    # print(f"  Lookback: {len(lookback_df)} records")
    # print(f"  Forecast: {len(forecast_df)} records")

    # Convert timestamp to datetime
    lookback_df["timestamp"] = pd.to_datetime(lookback_df["timestamp"])
    forecast_df["timestamp"] = pd.to_datetime(forecast_df["timestamp"])

    # Only keep sites that have FLOW-OBSERVED data in lookback
    observed_sites = lookback_df[lookback_df["variable"] == "FLOW-OBSERVED"][
        "site_id"
    ].unique()
    forecast_sites = forecast_df["site_id"].unique()
    common_sites = sorted(list(set(observed_sites) & set(forecast_sites)))

    # print(f"  Sites with FLOW-OBSERVED: {len(observed_sites)}")
    # print(f"  Sites with observed + forecast: {len(common_sites)}")

    if len(common_sites) == 0:
        # print("  Warning: No sites with observed data found")
        return 0

    # Plot hydrographs for selected sites
    n_sites = min(6, len(common_sites))
    selected_sites = common_sites[:n_sites]

    fig, axes = plt.subplots(n_sites, 1, figsize=(14, 3 * n_sites))
    if n_sites == 1:
        axes = [axes]

    fig.suptitle(
        "Hydrographs: Observed, Lookback Modeled, and Forecast",
        fontsize=14,
        fontweight="bold",
    )

    for ax, site in zip(axes, selected_sites):
        # Get lookback data for this site
        lookback_site = lookback_df[lookback_df["site_id"] == site].sort_values(
            "timestamp"
        )

        # Get forecast data for this site
        forecast_site = forecast_df[forecast_df["site_id"] == site].sort_values(
            "timestamp"
        )

        # Extract observed flow from lookback — exact match only
        obs_data = lookback_site[lookback_site["variable"] == "FLOW-OBSERVED"]
        if len(obs_data) > 0:
            ax.plot(
                obs_data["timestamp"],
                obs_data["value"],
                marker="o",
                linestyle="-",
                linewidth=2.5,
                markersize=3,
                label="Observed",
                color="black",
                alpha=0.8,
                zorder=3,
            )

        # Extract modeled lookback flow — prefer FLOW-COMBINE, fall back to FLOW
        for var in ["FLOW-COMBINE", "FLOW"]:
            modeled_data = lookback_site[lookback_site["variable"] == var]
            if len(modeled_data) > 0:
                break
        if len(modeled_data) > 0:
            ax.plot(
                modeled_data["timestamp"],
                modeled_data["value"],
                marker="s",
                linestyle="--",
                linewidth=2,
                markersize=2.5,
                label="Lookback Modeled",
                color="blue",
                alpha=0.7,
                zorder=2,
            )

        # Extract forecast flow — prefer FLOW-COMBINE, fall back to FLOW
        for var in ["FLOW-COMBINE", "FLOW"]:
            forecast_var = forecast_site[forecast_site["variable"] == var]
            if len(forecast_var) > 0:
                break
        if len(forecast_var) > 0:
            ax.plot(
                forecast_var["timestamp"],
                forecast_var["value"],
                marker="^",
                linestyle="-.",
                linewidth=2,
                markersize=2.5,
                label="Forecast",
                color="orange",
                alpha=0.7,
                zorder=2,
            )

        ax.set_title(f"Site: {site}", fontsize=11, fontweight="bold")
        ax.set_xlabel("Time", fontsize=10)
        ax.set_ylabel("Flow (cfs)", fontsize=10)
        ax.legend(loc="best", fontsize=9)
        ax.grid(True, alpha=0.3)

    fig.autofmt_xdate()
    plt.tight_layout()
    output_file = f"{OUTPUT_PATH}/02_hydrographs.png"
    plt.savefig(output_file, dpi=100, bbox_inches="tight")
    plt.close()

    # print(f"Hydrographs saved: {output_file}")
    return n_sites


def process_nash_sutcliffe():
    """Process Nash-Sutcliffe Efficiency statistics."""
    # print("Loading statistics...")
    stats_df = pd.read_parquet(f"{RESULTS_PATH}/stats.parquet")

    # print(f"  Total records: {len(stats_df)}")

    # Filter for Nash-Sutcliffe Efficiency if available
    if "StatisticType" in stats_df.columns:
        nse_stats = stats_df[
            stats_df["StatisticType"].str.contains(
                "Nash Sutcliffe", case=False, na=False
            )
        ].copy()
    else:
        nse_stats = stats_df

    if len(nse_stats) == 0:
        # print(
        #     f"  Available statistic types: {stats_df.get('StatisticType', pd.Series()).unique() if 'StatisticType' in stats_df.columns else 'N/A'}"
        # )
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

    try:
        # 1. Cumulative forcing
        mrms_file = f"{FORCING_PATH}/mrms_qpe.nc"
        if os.path.exists(mrms_file):
            plot_cumulative_forcing(mrms_file)
        else:
            pass  # MRMS file not found

        # 2. HRRR animation
        hrrr_file = f"{FORCING_PATH}/hrrr_qpf.nc"
        if os.path.exists(hrrr_file):
            create_forecast_animation(hrrr_file)
        else:
            pass  # HRRR file not found

        # 3. Hydrographs
        # print("3. Hydrographs")
        plot_hydrographs()

        # 4. Nash-Sutcliffe stats
        # print("4. Nash-Sutcliffe Statistics")
        nse_stats = process_nash_sutcliffe()
        if len(nse_stats) > 0 and "Value" in nse_stats.columns:
            try:
                mean_nse = nse_stats["Value"].mean()
                # print(f"   Mean NSE: {mean_nse:.4f}")
            except Exception:
                pass

        # 5. Junction map
        # print("5. Junction Peak Flows Map")
        _, high_flow_count = create_junction_map()
        # print(f"   Junctions >50 cfs: {high_flow_count}")

    except Exception as e:
        return 1

    # print(f"Analysis complete. Output: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    exit(main())
