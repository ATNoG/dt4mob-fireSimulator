from models.fire_incident import Point
from models.fire_incident import ConeSection
from wind import get_nearest_station
import math
import logging
from shapely.geometry import Polygon, MultiPolygon

logger = logging.getLogger(__name__)

def _compute_rate_kmh(wind_speed):
    safe_speed = max(0, min(200, wind_speed))
    return 0.18 * safe_speed

def _normalize_deg(deg):
    return (deg % 360 + 360) % 360

def _ms_to_kmh(speed_ms):
    return speed_ms * 3.6


def _destination_point(lat, lng, bearing_deg, distance_km) -> Point:
    # Earth's mean radius in km
    R = 6371.0088
    
    # Convert degrees to radians
    brng = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lam1 = math.radians(lng)
    
    # Pre-calculate sine/cosine of distance ratio
    d_r = distance_km / R
    sin_d = math.sin(d_r)
    cos_d = math.cos(d_r)
    
    # Pre-calculate sine/cosine of starting latitude
    sin_phi1 = math.sin(phi1)
    cos_phi1 = math.cos(phi1)
    
    # Calculate destination latitude
    sin_phi2 = (sin_phi1 * cos_d) + (cos_phi1 * sin_d * math.cos(brng))
    phi2 = math.asin(sin_phi2)
    
    # Calculate destination longitude
    y = math.sin(brng) * sin_d * cos_phi1
    x = cos_d - (sin_phi1 * sin_phi2)
    lam2 = lam1 + math.atan2(y, x)
    
    # Convert back to degrees and normalize longitude
    dest_lat = math.degrees(phi2)
    dest_lng = (math.degrees(lam2) + 540) % 360 - 180
    
    return Point(lat=dest_lat, lon=dest_lng)

def generate_cone_from_ignition(lat, lng) -> tuple[list[ConeSection], int, float]:
    """
    Generate a cone based on an ignition point and wind from the nearest weather station.
    Return a cone geometry with zones of 30m, 60m, 90m and 120m.
    """
    wind_station = get_nearest_station(lat, lng)
    if not wind_station:
        raise ValueError("No weather station found near the ignition point.")
    
    logger.debug("[Cone_Builder] wind_direction: %s, wind_intensity: %s", wind_station["wind_direction"], wind_station["wind_intensity"])
    wind_direction = wind_station["wind_direction_deg"]
    wind_speed = max(0, min(200, float(wind_station["wind_intensity"]) or 0))
    rate = _compute_rate_kmh(wind_speed)

    horizons = [30, 60, 90, 120]
    dists = [rate * (h / 60) for h in horizons]
    

    center_bearing = _normalize_deg(wind_direction + 180)
    logger.debug("[Cone_Builder] center_bearing: %s", center_bearing)
    plus_bearing = _normalize_deg(center_bearing + 15)
    minus_bearing = _normalize_deg(center_bearing - 15)
    ignition_Point: Point = Point(lat=lat, lon=lng)

    segments: list[ConeSection] = []

    distances = [0] + dists

    for i in range(1,len(distances)):
        d_prev = distances[i-1]
        d_curr = distances[i]

        if d_prev == 0:
            p_curr = _destination_point(lat, lng, plus_bearing, d_curr)
            n_curr = _destination_point(lat, lng, minus_bearing, d_curr)
            poly = [ignition_Point, p_curr, n_curr, ignition_Point]
        else:
            p_prev = _destination_point(lat, lng, plus_bearing, d_prev)
            n_prev = _destination_point(lat, lng, minus_bearing, d_prev)
            p_curr = _destination_point(lat, lng, plus_bearing, d_curr)
            n_curr = _destination_point(lat, lng, minus_bearing, d_curr)
            poly: list[Point] = [p_prev, p_curr, n_curr, n_prev,p_prev]
        segments.append(ConeSection(
            horizonte_min=horizons[i-1],
            points=poly
        ))
    return segments, wind_direction, wind_speed

def cone_intersects_polygons(cone_segments: list[ConeSection], risk_polygons: list[Polygon|MultiPolygon]) -> bool:
    """
    Check if any of the cone segments intersects with any of the given risk areas (polygons).
    """
    cone_geoms = [
        Polygon([(p.lon, p.lat) for p in seg.points]) 
        for seg in cone_segments
    ]
    for risk_poly in risk_polygons:
        for cone_geom in cone_geoms:
            if cone_geom.intersects(risk_poly):
                return True
    return False


def segments_to_wkt(segments):
    """
    Converts the list of segments into a single MULTIPOLYGON WKT.
    """
    poly_strings = []
    
    for seg in segments:
        coords = seg['coords']
        # Extract points as strings "LNG LAT"
        pts = [f"{c['lng']:.14f} {c['lat']:.14f}" for c in coords]
        
        # Force closure: Ensure the last point is IDENTICAL to the first
        if pts[0] != pts[-1]:
            pts.append(pts[0])
            
        poly_strings.append(f"(({', '.join(pts)}))")
    
    # Return as a MULTIPOLYGON
    return f"MULTIPOLYGON({', '.join(poly_strings)})"