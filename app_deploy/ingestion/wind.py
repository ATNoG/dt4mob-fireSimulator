"""
wind.py — IPMA wind data ingestion and IDW interpolation.
"""
import logging
import math

import numpy as np
from pyproj import Geod
from services.ditto import ditto_client

# Direction code → degrees (clockwise from North)
DIR_MAP = {1: 0, 2: 45, 3: 90, 4: 135, 5: 180, 6: 225, 7: 270, 8: 315, 9: 0}

_geod = Geod(ellps="WGS84")


def _dir_code_to_uv(intensity_ms: float, dir_code: int) -> tuple[float, float]:
    """Convert wind intensity (m/s) and IPMA direction code to (u, v) components.

    IPMA idDireccVento is the direction FROM which the wind blows (meteorological
    convention). u/v must be the "towards" vector, so negate both components.
    Example: code 1 = from North → wind blows southward → u=0, v=-speed.
    """
    deg = DIR_MAP.get(dir_code, 0)
    rad = math.radians(deg)
    u = -intensity_ms * math.sin(rad)  # eastward component (negated: from→to)
    v = -intensity_ms * math.cos(rad)  # northward component (negated: from→to)
    return u, v

def get_nearest_stations(conn, lat: float, lon: float, n: int = 5) -> list[dict]:
    """
    Return the n nearest Station based on the seach api of eclipse Ditto
    Each returned dict has keys:
        lat, lon, wind_u, wind_v, dist_km, id_estacao, local_estacao
    """
    logging.info(
        "[wind] Querying %d nearest stations to (%.4f, %.4f)", n, lat, lon
    )
    # quadkey bounds until the n stations are found
    zoom = 10
    while True:
        things = ditto_client.get_stations_zoom_sync(lat, lon, zoom)
        if len(things) >= n:
            break
        if len(things) > n:
            things.sort(key=lambda t: math.sqrt((t.get("attributes", {}).get("location", {}).get("lat", 0) - lat) ** 2 + (t.get("attributes", {}).get("location", {}).get("lon", 0) - lon) ** 2))
            things = things[:n]
            break
        zoom -= 1
        logging.debug("[wind] Found stations: %s at zoom %d", things, zoom)
        if zoom < 0:
            logging.warning("[wind] No stations found in Ditto search up to zoom 0")
            return []
    logging.info("[wind] Found %d stations in Ditto search at zoom %d", len(things), zoom)
    logging.info("[wind] Stations: %s", things)
    returnarr = []
    for thing in things:
        feature_properties = thing.get("features", {}).get("meteorology", {}).get("properties", {})
        location = thing.get("attributes", {}).get("location", {})
        wind_u, wind_v = _dir_code_to_uv(float(feature_properties.get("wind_intensity", 0)), int(feature_properties.get("wind_direction", 9)))
        returnarr.append({
            "id_estacao": thing.get("id"),
            "local_estacao": thing.get("attributes", {}).get("location_name", ""),
            "lat": location.get("latitude"),
            "lon": location.get("longitude"),
            "wind_u": wind_u,
            "wind_v": wind_v,
            "wind_intensity": feature_properties.get("wind_intensity"),
            "wind_direction": feature_properties.get("wind_direction"),
            "wind_direction_deg": (math.degrees(math.atan2(-wind_u, -wind_v)) + 360) % 360,
            "dist_km": math.sqrt((location.get("latitude", 0) - lat) ** 2 + (location.get("longitude", 0) - lon) ** 2) * 111,  # rough km conversion
        })
        returnarr.sort(key=lambda s: s["dist_km"])
    return returnarr[:n]

def get_nearest_station(lat: float, lon: float) -> dict:
    """
    Return the single nearest station to the given lat/lon, or None if no stations found.
    """
    conn = None
    stations = get_nearest_stations(conn,lat, lon, n=1)
    return stations[0] if stations else {}

def idw_wind_grid(
    stations: list[dict],
    grid_lats: np.ndarray,
    grid_lons: np.ndarray,
    power: float = 2.0,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Inverse Distance Weighting interpolation of wind (u, v) onto a grid.

    Parameters
    ----------
    stations   : list of dicts from get_nearest_stations()
    grid_lats  : 2-D array of latitude values (shape H×W)
    grid_lons  : 2-D array of longitude values (shape H×W)
    power      : IDW exponent (default 2)

    Returns
    -------
    (wind_u_grid, wind_v_grid) each shaped like grid_lats
    """
    if not stations:
        logging.warning("[wind] No stations for IDW — returning zero wind grid")
        zero = np.zeros_like(grid_lats, dtype=np.float32)
        return zero, zero.copy()

    # Shortcut: single station → fill entire grid with that value
    if len(stations) == 1:
        logging.info("[wind] Single station — filling grid with constant wind")
        u_grid = np.full_like(grid_lats, fill_value=stations[0]["wind_u"], dtype=np.float32)
        v_grid = np.full_like(grid_lats, fill_value=stations[0]["wind_v"], dtype=np.float32)
        return u_grid, v_grid

    st_lats = np.array([s["lat"] for s in stations])
    st_lons = np.array([s["lon"] for s in stations])
    st_u = np.array([s["wind_u"] for s in stations])
    st_v = np.array([s["wind_v"] for s in stations])

    H, W = grid_lats.shape
    u_grid = np.zeros((H, W), dtype=np.float64)
    v_grid = np.zeros((H, W), dtype=np.float64)

    # Compute geodesic distances for every grid cell to every station
    # Vectorise per station to avoid a full nested loop
    for i, (slat, slon, su, sv) in enumerate(zip(st_lats, st_lons, st_u, st_v)):
        # _geod.inv expects (lon1, lat1, lon2, lat2) arrays
        _, _, dist_m = _geod.inv(
            np.full(H * W, slon),
            np.full(H * W, slat),
            grid_lons.ravel(),
            grid_lats.ravel(),
        )
        dist_m = dist_m.reshape(H, W)

        # Replace exact coincidences with a tiny value to avoid division by zero
        dist_m = np.where(dist_m == 0.0, 1e-3, dist_m)

        weight = 1.0 / dist_m**power
        u_grid += weight * su
        v_grid += weight * sv

    # Normalise by sum of weights
    weight_sum = np.zeros((H, W), dtype=np.float64)
    for slat, slon in zip(st_lats, st_lons):
        _, _, dist_m = _geod.inv(
            np.full(H * W, slon),
            np.full(H * W, slat),
            grid_lons.ravel(),
            grid_lats.ravel(),
        )
        dist_m = dist_m.reshape(H, W)
        dist_m = np.where(dist_m == 0.0, 1e-3, dist_m)
        weight_sum += 1.0 / dist_m**power

    u_grid /= weight_sum
    v_grid /= weight_sum

    logging.info("[wind] IDW interpolation complete, grid shape %s", grid_lats.shape)
    return u_grid.astype(np.float32), v_grid.astype(np.float32)
