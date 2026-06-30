import os
from shapely.geometry import Polygon, MultiPolygon, shape, mapping
from models.fire_incident import ConeSection
import json

def read_risky_areas_from_geojson(file_path) -> list[Polygon|MultiPolygon]:


    with open(file_path, 'r') as f:
        data = json.load(f)

    risky_areas = []
    for feature in data['features']:
        geom = shape(feature['geometry'])
        if isinstance(geom, (Polygon, MultiPolygon)):
            risky_areas.append(geom)
        else:
            print(f"Warning: Geometry type {geom.geom_type} is not supported and will be skipped.")

    return risky_areas

def save_cone(cones:list[ConeSection],base_path:str):
    feature_collection = {
        "type": "FeatureCollection",
        "valid_at": "2026-05-18T15:23:00Z",
        "features": [],
    }
    for cone in cones:
        hori_min = cone.horizonte_min
        shapely_poly = Polygon([[p.lon,p.lat] for p in cone.points])
        feature = {
            "type": "Feature",
            "properties": {"numberOfPolygons": 1, "horizonte_min": hori_min},
            "geometry": mapping(
                shapely_poly
            ),
        }
        feature_collection["features"].append(feature)
    output_filename = "cone_horizon.geojson"
    path = os.path.join(base_path,output_filename)
    with open(path, "w") as out_file:
        json.dump(feature_collection, out_file, indent=2)
