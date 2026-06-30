"""
landscape.py — Build landscape.nc (NetCDF) and fuels.csv for ForeFire.

All raster data is reprojected to EPSG:3763 (Portugal TM06, metres) to match
the metric domain ForeFire expects. The NetCDF format mirrors the official
ForeFire data.nc structure:

  domain   — scalar string variable; SWx/SWy/Lx/Ly in EPSG:3763 metres
  altitude — (1, 1, ny, nx)  int16   elevation in metres
  fuel     — (1, 1, fy, fx)  int16   COSc code (= fuels.csv Index)
  wind     — (2, 2, wr, wc)  float32 [u/v, time_step, rows, cols]

ForeFire derives slope and aspect from altitude internally.
"""

import logging
import os

import netCDF4 as nc
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.warp import Resampling, reproject as rasterio_reproject

_to_metric = Transformer.from_crs("EPSG:4326", "EPSG:3763", always_xy=True)
_to_wgs84  = Transformer.from_crs("EPSG:3763", "EPSG:4326", always_xy=True)

logger = logging.getLogger(__name__)

_DST_CRS = CRS.from_epsg(3763)


def _read_reprojected(
    tif_path: str,
    sw_x: float, sw_y: float, lx_m: float, ly_m: float,
    resolution_m: float,
    resampling: Resampling = Resampling.bilinear,
) -> np.ndarray:
    """
    Read a GeoTIFF band, reproject to EPSG:3763, clip to metric bbox.

    Returns a 2-D array with shape (rows, cols) where:
        rows = round(ly_m / resolution_m)
        cols = round(lx_m / resolution_m)
    """
    cols = round(lx_m / resolution_m)
    rows = round(ly_m / resolution_m)
    dst_transform = transform_from_bounds(
        sw_x, sw_y, sw_x + lx_m, sw_y + ly_m, cols, rows
    )

    with rasterio.open(tif_path) as src:
        dst_dtype = src.dtypes[0]
        dst_array = np.zeros((rows, cols), dtype=dst_dtype)
        rasterio_reproject(
            source=rasterio.band(src, 1),
            destination=dst_array,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs=_DST_CRS,
            resampling=resampling,
        )

    return dst_array


def build_landscape_nc(
    ocorrencia_id: str,
    ignition_lat: float,
    ignition_lon: float,
    wind_u_grid: np.ndarray,
    wind_v_grid: np.ndarray,
    bbox: tuple[float, float, float, float],
    output_path: str,
) -> None:
    """
    Build landscape.nc for ForeFire.

    bbox is (xmin, ymin, xmax, ymax) in EPSG:4326 degrees.
    All raster data is reprojected to EPSG:3763 before writing.
    """
    data_dir = os.environ.get("DATA_PROCESSED", "/data/processed")
    xmin, ymin, xmax, ymax = bbox

    logger.info("[landscape] Building landscape.nc for sim %s, bbox=%s", ocorrencia_id, bbox)

    # ── Metric domain ─────────────────────────────────────────────────────────
    sw_x, sw_y = _to_metric.transform(xmin, ymin)
    ne_x, ne_y = _to_metric.transform(xmax, ymax)
    lx_m = abs(ne_x - sw_x)
    ly_m = abs(ne_y - sw_y)

    logger.info(
        "[landscape] Metric domain: SW=(%.1f, %.1f) Lx=%.1f Ly=%.1f (EPSG:3763)",
        sw_x, sw_y, lx_m, ly_m,
    )

    # ── Read and reproject rasters to EPSG:3763 ───────────────────────────────
    # altitude at ~100 m (matches official data.nc ratio)
    ALT_RES  = 100.0   # metres per pixel for altitude
    FUEL_RES = 10.0    # metres per pixel for fuel (full native resolution)

    logger.info("[landscape] Reprojecting altitude at %g m resolution", ALT_RES)
    elev_arr = _read_reprojected(
        os.path.join(data_dir, "dem.tif"),
        sw_x, sw_y, lx_m, ly_m,
        resolution_m=ALT_RES,
        resampling=Resampling.bilinear,
    )
    ny, nx = elev_arr.shape
    logger.info("[landscape] altitude shape: %s", elev_arr.shape)

    logger.info("[landscape] Reprojecting fuel at %g m resolution", FUEL_RES)
    fuel_arr = _read_reprojected(
        os.path.join(data_dir, "fuel_type.tif"),
        sw_x, sw_y, lx_m, ly_m,
        resolution_m=FUEL_RES,
        resampling=Resampling.nearest,
    )
    fy, fx = fuel_arr.shape
    logger.info("[landscape] fuel shape: %s", fuel_arr.shape)

    # ── Flip arrays: rasterio stores row-0 at north (top), but ForeFire/NetCDF
    #    convention expects row-0 at south (y=0 = SW corner). ──────────────────
    elev_arr = np.flipud(elev_arr)
    fuel_arr = np.flipud(fuel_arr)

    # ── Wind grid (keep at pipeline IDW resolution) ───────────────────────────
    wr, wc = wind_u_grid.shape

    # ── Write NetCDF ──────────────────────────────────────────────────────────
    os.makedirs(
        os.path.dirname(output_path) if os.path.dirname(output_path) else ".",
        exist_ok=True,
    )
    logger.info("[landscape] Writing NetCDF to %s", output_path)

    with nc.Dataset(output_path, "w", format="NETCDF4") as ds:

        # Dimensions
        ds.createDimension("nt", 1)
        ds.createDimension("nz", 1)
        ds.createDimension("ny", ny)
        ds.createDimension("nx", nx)
        ds.createDimension("ft", 1)
        ds.createDimension("fz", 1)
        ds.createDimension("fy", fy)
        ds.createDimension("fx", fx)
        ds.createDimension("wind_dimensions", 2)
        ds.createDimension("wind_directions", 2)
        ds.createDimension("wind_rows", wr)
        ds.createDimension("wind_columns", wc)

        # ── Coordinate variables (south→north, west→east) for QGIS display ──
        # altitude grid — row 0 is at ymin (south) after flipud
        alt_lats = np.linspace(ymin, ymax, ny, dtype=np.float64)
        alt_lons = np.linspace(xmin, xmax, nx, dtype=np.float64)
        lat_v = ds.createVariable("lat", "f8", ("ny",))
        lat_v.units = "degrees_north"
        lat_v.standard_name = "latitude"
        lat_v[:] = alt_lats
        lon_v = ds.createVariable("lon", "f8", ("nx",))
        lon_v.units = "degrees_east"
        lon_v.standard_name = "longitude"
        lon_v[:] = alt_lons

        # domain variable attrs
        # BBoxWSEN: geographic bounds (WGS84) — used by ForeFire as reference
        # WSENLBRT: W,S,E,N geographic + L,B,R,T in LOCAL metres (SW=0,0)
        bbox_wsen = f"{xmin},{ymin},{xmax},{ymax}"
        wsenlbrt  = (
            f"{xmin},{ymin},{xmax},{ymax},"
            f"0.0,0.0,{lx_m:.2f},{ly_m:.2f}"
        )

        dv = ds.createVariable("domain", str, ())
        dv[...] = np.array("", dtype=object)
        # ForeFire uses a LOCAL metric coordinate system: SW corner = (0, 0).
        # Geographic reference is provided via BBoxWSEN; ForeFire converts
        # lon/lat ignition coords to local X/Y as:
        #   x = (lon - BBoxWSEN_W) * metersPerDegreeLon
        # so SWx/SWy must be 0 (local origin), NOT absolute EPSG:3763 values.
        dv.SWx      = np.float32(0.0)
        dv.SWy      = np.float32(0.0)
        dv.Lx       = np.float32(lx_m)
        dv.Ly       = np.float32(ly_m)
        dv.BBoxWSEN = bbox_wsen
        dv.Lz       = np.float32(0.0)
        dv.t0       = np.float32(0.0)
        dv.Lt       = np.float32(np.inf)
        dv.SWz      = np.float32(0.0)
        dv.type     = "domain"
        dv.WSENLBRT = wsenlbrt

        # altitude (1, 1, ny, nx) int16  — row 0 = south (after flipud)
        alt_v = ds.createVariable("altitude", "i2", ("nt", "nz", "ny", "nx"))
        alt_v.type = "data"
        alt_v.coordinates = "lat lon"
        alt_v[0, 0, :, :] = np.clip(elev_arr, -32768, 32767).astype(np.int16)

        # fuel (1, 1, fy, fx) int16  — row 0 = south (after flipud)
        # fuel has finer resolution so its lat/lon span same bbox
        fuel_lats = np.linspace(ymin, ymax, fy, dtype=np.float64)
        fuel_lons = np.linspace(xmin, xmax, fx, dtype=np.float64)
        flat_v = ds.createVariable("fuel_lat", "f8", ("fy",))
        flat_v.units = "degrees_north"; flat_v.standard_name = "latitude"
        flat_v[:] = fuel_lats
        flon_v = ds.createVariable("fuel_lon", "f8", ("fx",))
        flon_v.units = "degrees_east"; flon_v.standard_name = "longitude"
        flon_v[:] = fuel_lons

        fuel_v = ds.createVariable("fuel", "i2", ("ft", "fz", "fy", "fx"))
        fuel_v.type = "fuel"
        fuel_v.coordinates = "fuel_lat fuel_lon"
        fuel_v[0, 0, :, :] = fuel_arr.astype(np.int16)

        # wind (2, 2, wr, wc) float32
        wind_data = np.zeros((2, 2, wr, wc), dtype=np.float32)
        wind_data[0, 0, :, :] = wind_u_grid.astype(np.float32)
        wind_data[0, 1, :, :] = wind_u_grid.astype(np.float32)
        wind_data[1, 0, :, :] = wind_v_grid.astype(np.float32)
        wind_data[1, 1, :, :] = wind_v_grid.astype(np.float32)

        wind_v = ds.createVariable(
            "wind", "f4",
            ("wind_dimensions", "wind_directions", "wind_rows", "wind_columns"),
            fill_value=np.nan,
        )
        wind_v.type = "wind"
        wind_v[:] = wind_data

    logger.info(
        "[landscape] landscape.nc written — altitude %dx%d @ %gm, fuel %dx%d @ %gm",
        ny, nx, ALT_RES, fy, fx, FUEL_RES,
    )


# ── fuels.csv ─────────────────────────────────────────────────────────────────
_FUELS_CSV_HEADER = (
    "Index;Rhod;Rhol;Md;Ml;sd;sl;e;Sigmad;Sigmal;"
    "stoch;RhoA;Ta;Tau0;Deltah;DeltaH;Cp;Cpa;Ti;X0;r00;Blai;me"
)
_FUELS_CSV_ROWS = [
    "0;500.0;500.0;0.13;0.5;0;0;0.0;0.0;0.0;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.3",
    "211;500.0;500.0;0.10;0.5;3500;5700;0.20;0.18;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.12",
    "212;500.0;500.0;0.25;0.8;3000;5700;0.10;0.12;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.25",
    "213;500.0;500.0;0.13;0.5;2800;5700;0.30;0.25;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.15",
    "311;500.0;500.0;0.12;0.5;1800;5700;0.40;0.55;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.30",
    "312;500.0;500.0;0.10;0.5;5000;5700;0.50;1.85;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.22",
    "313;500.0;500.0;0.12;0.5;2200;5700;0.40;0.60;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.25",
    "321;500.0;500.0;0.10;0.5;1500;5700;0.30;0.85;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.25",
    "322;500.0;500.0;0.10;0.5;1400;5700;0.20;0.50;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.25",
    "323;500.0;500.0;0.10;0.5;1600;5700;0.30;0.75;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.20",
    "410;500.0;500.0;0.10;0.5;2000;5700;1.20;1.30;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.20",
    "420;500.0;500.0;0.10;0.5;3500;5700;0.30;0.20;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.12",
    "610;500.0;500.0;0.30;0.8;2500;5700;0.40;0.15;1.28;8.3;1.0;300.0;70000.0;2300000.0;1.5E7;1800.0;1000.0;600.0;0.3;2.5E-5;4.0;0.40",
]


def write_fuels_csv(output_path: str) -> None:
    logger.info("[landscape] Writing fuels.csv to %s", output_path)
    os.makedirs(
        os.path.dirname(output_path) if os.path.dirname(output_path) else ".",
        exist_ok=True,
    )
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(_FUELS_CSV_HEADER + "\n")
        for row in _FUELS_CSV_ROWS:
            fh.write(row + "\n")
    logger.info("[landscape] fuels.csv written with %d fuel entries", len(_FUELS_CSV_ROWS))
