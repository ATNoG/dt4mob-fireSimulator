from rasterio.enums import Resampling
import os
from pyproj import Transformer
from landscape import _read_reprojected
import logging
import netCDF4 as nc
import numpy as np
import trimesh
import json
from shapely.geometry import shape, MultiPolygon, Polygon
from models.fire_incident import ConeSection
import scipy.interpolate as interp
from scipy.spatial import Delaunay
from models.constants import FIREFRONT_COLORS,FIRE_FRONTS,IGNITION_DOT_RADIUS,IGNITION_COLOR
from settings import config

_to_metric = Transformer.from_crs("EPSG:4326", "EPSG:3763", always_xy=True)

def _triangulate_polygon_with_density(poly, grid_spacing=0.0005):
    """
    Densifies the polygon with internal points and triangulates.
    grid_spacing: Approx degrees between points (0.0001 is ~11m)
    """
    boundary_points = []
    exterior = poly.exterior
    distance = 0
    while distance < exterior.length:
        point = exterior.interpolate(distance)
        boundary_points.append([point.x, point.y])
        distance += grid_spacing
    
    minx, miny, maxx, maxy = poly.bounds
    x_coords = np.arange(minx, maxx, grid_spacing)
    y_coords = np.arange(miny, maxy, grid_spacing)
    xv, yv = np.meshgrid(x_coords, y_coords)
    grid_points = np.vstack([xv.ravel(), yv.ravel()]).T
    
    mask = [poly.contains(shape({"type": "Point", "coordinates": p})) for p in grid_points]
    internal_points = grid_points[mask]
    
    all_points = np.vstack([boundary_points, internal_points])
    tri = Delaunay(all_points)
    
    midpoints = all_points[tri.simplices].mean(axis=1)
    valid_faces_mask = [poly.contains(shape({"type": "Point", "coordinates": m})) for m in midpoints]
    faces = tri.simplices[valid_faces_mask]
    
    return all_points, faces

def export_perimeters_geojson_to_gltf(sim_id, ignition_lat, ignition_lon, hex_color="#2ecc71"):
    logger = logging.getLogger(__name__)
    # 1. Load NetCDF data
    base_path = os.path.join(config.simulations_dir,sim_id)
    nc_path = f"{base_path}/landscape.nc"
    output_path = f"{base_path}/"
    with nc.Dataset(nc_path, "r") as ds:
        # Note: ForeFire NC stores altitude as (nt, nz, ny, nx)
        z_values = ds.variables["altitude"][0, 0, :, :].astype(float)
        lats = ds.variables["lat"][:]
        lons = ds.variables["lon"][:]

    # Create an interpolator (RectBivariateSpline is fast for grids)
    # We use lons/lats to map coordinates to Z
    interp_func = interp.RectBivariateSpline(lats, lons, z_values)

    # 2. Parse GeoJSON and Triangulate
    Z_OFFSET_STEP = 0.3
    
    avg_lat = np.mean(lats)
    avg_lon = np.mean(lons)
    
    # Calculate exactly where the ignition point is in your "meter" space
    ignition_z = float(interp_func.ev(ignition_lat, ignition_lon))
    ign_x = (ignition_lon - avg_lon) * 111000 * np.cos(np.radians(avg_lat))
    ign_y = (ignition_lat - avg_lat) * 111000
    ign_z = ignition_z  # We use the ground level as 0
    logger.info(f"Ignition point height from ocean level: {ignition_z} meters")

    origin_translation = [ign_x, ign_y, ign_z]

    final_mesh = trimesh.Scene(base_frame=f"fire_simulation_{sim_id}") 
    for i, fire_front in enumerate(FIRE_FRONTS):
        geojson_data = f"{base_path}/step_{fire_front}.geojson"
        with open(geojson_data, 'r') as f:
            geojson_data = json.load(f)
        poly: MultiPolygon = shape(geojson_data['features'][0]['geometry'])
        
        # trimesh.creation.triangulate_polygon creates a 2D mesh of the polygon area
        # using the actual vertices of the GeoJSON.
        vertices_2d, faces = _triangulate_polygon_with_density(poly.geoms[0], grid_spacing=0.0002)
        
        # vertices_2d is (N, 2) -> [lon, lat]
        # 3. Sample Z for every vertex
        z_sampled = interp_func.ev(vertices_2d[:, 1], vertices_2d[:, 0])

        z_final = z_sampled + len(FIRE_FRONTS) * Z_OFFSET_STEP - (i * Z_OFFSET_STEP)
        logger.info(f"Max Elevation in Data: {np.max(z_final)} meters")
        
        # Combine into 3D vertices
        vertices_3d = np.column_stack((vertices_2d, z_final))

        # 4. Build the Mesh
        mesh = trimesh.Trimesh(vertices=vertices_3d, faces=faces)
        
        # Convert to local meters (approximate)
        mesh.vertices[:, 0] = (mesh.vertices[:, 0] - avg_lon) * 111000 * np.cos(np.radians(avg_lat))
        mesh.vertices[:, 1] = (mesh.vertices[:, 1] - avg_lat) * 111000
        # Z is already in meters, no scaling needed unless you want exaggeration

        mesh.apply_translation(-np.array(origin_translation))

        # 6. Apply Style
        h = FIREFRONT_COLORS.get(fire_front, hex_color).lstrip('#')
        rgba = [int(h[i:i+2], 16) for i in (0, 2, 4)] + [255]
        mesh.visual.face_colors = rgba

        if i == 0:
            dot_mesh = trimesh.creation.uv_sphere(radius=IGNITION_DOT_RADIUS)
            dot_mesh.apply_translation([0, 0, 2.0]) # 2 meters above origin
            dot_mesh.visual.face_colors = IGNITION_COLOR
            mesh = trimesh.util.concatenate([mesh, dot_mesh])
        
        final_mesh.add_geometry(mesh, node_name=f"front_{fire_front}")

    rotation_angle = np.radians(-90) 
    direction_vector = [1, 0, 0] # Rotation around the X-axis

    # Create the transformation matrix
    transform = trimesh.transformations.rotation_matrix(rotation_angle, direction_vector)
    final_mesh.apply_transform(transform)

    rotation_angle = np.radians(90) 
    direction_vector = [0, 1, 0] # Rotation around the Y-axis

    # Create the transformation matrix
    transform = trimesh.transformations.rotation_matrix(rotation_angle, direction_vector)
    final_mesh.apply_transform(transform)
    # 7. Export
    final_mesh.export(f"{output_path}fire_simulation.glb", file_type='glb')
    #logger.info(f"High-res mesh created: {len(final_mesh.vertices)} vertices, {len(final_mesh.faces)} faces")

# Example usage:
# export_geojson_to_gltf("landscape.nc", your_geojson_dict, "fire_zone.glb", "#E67E22")


def export_cone_geojson_to_gltf(sim_id, ignition_lat, ignition_lon, cones:list[ConeSection]):
    logger = logging.getLogger(__name__)
    
    base_path = os.path.join(config.simulations_dir,sim_id)
    data_dir = "/data/processed"
    output_path = f"{base_path}/"

    # 1. Gather all Lat/Lon points from all cones to calculate a geographic bounding box
    all_lons = [ignition_lon]
    all_lats = [ignition_lat]
    for cone in cones:
        for pt in cone.points:
            all_lons.append(pt.lon)
            all_lats.append(pt.lat)

    # Add a small buffer safety net (~0.001 degrees is roughly 100 meters)
    buffer_deg = 0.001
    xmin, ymin = min(all_lons) - buffer_deg, min(all_lats) - buffer_deg
    xmax, ymax = max(all_lons) + buffer_deg, max(all_lats) + buffer_deg

    # 2. Transform spatial extent to Metric Domain (Matching landscape.py requirements)
    sw_x, sw_y = _to_metric.transform(xmin, ymin)
    ne_x, ne_y = _to_metric.transform(xmax, ymax)
    lx_m = abs(ne_x - sw_x)
    ly_m = abs(ne_y - sw_y)

    # 3. Read and reproject raw DEM GeoTIFF directly via metric bounding box
    ALT_RES = 100.0  # Meter resolution matching ForeFire altitude standard 
    elev_arr = _read_reprojected(
        os.path.join(data_dir, "dem.tif"),
        sw_x, sw_y, lx_m, ly_m,
        resolution_m=ALT_RES,
        resampling=Resampling.bilinear,
    )

    # 4. Invert raster rows vertically (ForeFire / NetCDF convention: bottom row = South)
    elev_arr = np.flipud(elev_arr)
    ny, nx = elev_arr.shape

    # 5. Build dynamic geographic coordinate vectors matching the linear array layout
    lats = np.linspace(ymin, ymax, ny, dtype=np.float64)
    lons = np.linspace(xmin, xmax, nx, dtype=np.float64)

    # Create the Interpolator using WGS84 degree inputs
    interp_func = interp.RectBivariateSpline(lats, lons, elev_arr)
    
    avg_lat = np.mean(lats)
    avg_lon = np.mean(lons)
    
    # Calculate local spatial transformation coordinates
    ignition_z = float(interp_func.ev(ignition_lat, ignition_lon))
    ign_x = (ignition_lon - avg_lon) * 111000 * np.cos(np.radians(avg_lat))
    ign_y = (ignition_lat - avg_lat) * 111000
    ign_z = ignition_z  
    logger.info(f"Ignition point height from ocean level: {ignition_z} meters")

    origin_translation = [ign_x, ign_y, ign_z]

    final_mesh = trimesh.Scene(base_frame=f"fire_simulation_{sim_id}")
    colors = list(FIREFRONT_COLORS.values())
    for i, cone in enumerate(cones):
        poly: MultiPolygon = MultiPolygon([Polygon([[p.lon,p.lat] for p in cone.points])])
        
        # trimesh.creation.triangulate_polygon creates a 2D mesh of the polygon area
        # using the actual vertices of the GeoJSON.
        vertices_2d, faces = _triangulate_polygon_with_density(poly.geoms[0], grid_spacing=0.0002)
        
        # vertices_2d is (N, 2) -> [lon, lat]
        # 3. Sample Z for every vertex
        z_sampled = interp_func.ev(vertices_2d[:, 1], vertices_2d[:, 0])

        z_final = z_sampled
        logger.info(f"Max Elevation in Data: {np.max(z_final)} meters")
        
        # Combine into 3D vertices
        vertices_3d = np.column_stack((vertices_2d, z_final))

        # 4. Build the Mesh
        mesh = trimesh.Trimesh(vertices=vertices_3d, faces=faces)
        
        # Convert to local meters (approximate)
        mesh.vertices[:, 0] = (mesh.vertices[:, 0] - avg_lon) * 111000 * np.cos(np.radians(avg_lat))
        mesh.vertices[:, 1] = (mesh.vertices[:, 1] - avg_lat) * 111000
        # Z is already in meters, no scaling needed unless you want exaggeration

        mesh.apply_translation(-np.array(origin_translation))

        # 6. Apply Style
        h = colors[i].replace("#","")
        rgba = [int(h[i:i+2], 16) for i in (0, 2, 4)] + [255]
        mesh.visual.face_colors = rgba

        if i == 0:
            dot_mesh = trimesh.creation.uv_sphere(radius=IGNITION_DOT_RADIUS)
            dot_mesh.apply_translation([0, 0, 2.0]) # 2 meters above origin
            dot_mesh.visual.face_colors = IGNITION_COLOR
            mesh = trimesh.util.concatenate([mesh, dot_mesh])
        
        final_mesh.add_geometry(mesh, node_name=f"cone_{cone.horizonte_min}")

    rotation_angle = np.radians(-90) 
    direction_vector = [1, 0, 0] # Rotation around the X-axis

    # Create the transformation matrix
    transform = trimesh.transformations.rotation_matrix(rotation_angle, direction_vector)
    final_mesh.apply_transform(transform)

    rotation_angle = np.radians(90) 
    direction_vector = [0, 1, 0] # Rotation around the Y-axis

    # Create the transformation matrix
    transform = trimesh.transformations.rotation_matrix(rotation_angle, direction_vector)
    final_mesh.apply_transform(transform)
    # 7. Export
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    final_mesh.export(f"{output_path}fire_cone.glb", file_type='glb')