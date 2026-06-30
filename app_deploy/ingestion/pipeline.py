"""
pipeline.py — Full Stage 2 fire simulation pipeline for DT4MOB.

This module is called from a background thread (asyncio.to_thread) after the
POST /api/ignition endpoint determines that the quick-cone intersects a Brisa
highway corridor.
"""
import shutil
from models.ditto_body_maker import DittoBodyBuilder,fireState
from settings import config

import logging
import os
from datetime import datetime, timezone, timedelta

import numpy as np

from models.constants import FIRE_FRONTS
from wind import get_nearest_stations, idw_wind_grid
from landscape import build_landscape_nc, write_fuels_csv
from forefire_runner import build_ff_script, run_forefire, parse_perimeters
from services.ditto import ditto_client
from s3_uploader import upload_simulation_dir_to_seaweed_minio
from gltf_experter import export_perimeters_geojson_to_gltf,export_cone_geojson_to_gltf
from cone_management import generate_cone_from_ignition, cone_intersects_polygons
from geojson import read_risky_areas_from_geojson,save_cone

logger = logging.getLogger(__name__)

# Base directory for per-simulation working files.
# Must be on the shared /data volume so the forefire container can read/write it too.

def run_pipeline(
    ocorrencia_id: str,
    ignition_lat: float,
    ignition_lon: float,
) -> bool:
    """
    Execute the full ForeFire simulation pipeline for a given ignition event.

    Steps
    -----
    1. Create simulacoes record with status='running'.
    2. Find nearest stations, compute IDW wind grid.
    3. Build landscape.nc from on-disk COG rasters.
    4. Write fuels.csv (hardcoded COSc 2025 Rothermel parameters).
    5. Generate forefire_script.ff.
    6. Run ForeFire binary.
    7. Parse output perimeters.json.
    8. Insert perimeters into simulacao_perimetros_final.
    9. Update simulation status to 'done' or 'error'.

    Returns
    -------
    simulation_id (int)
    """
    conn = None
    # NOTE: do NOT reset ocorrencia_id here — it is passed in from main.py
    logger.info("[pipeline] %s | Simulation id=%s status → running", datetime.now(timezone.utc).isoformat(), ocorrencia_id)

    # ------------------------------------------------------------------
    # 3. Nearest stations + IDW grid
    # ------------------------------------------------------------------
    logger.info("[pipeline] %s | Querying nearest stations", datetime.now(timezone.utc).isoformat())
    stations = get_nearest_stations(conn, ignition_lat, ignition_lon, n=5)

    # Define simulation domain — 0.15° buffer (~15 km) is enough for a 2h run
    buf = 0.15  # degrees
    bbox = (
        ignition_lon - buf,  # xmin
        ignition_lat - buf,  # ymin
        ignition_lon + buf,  # xmax
        ignition_lat + buf,  # ymax
    )
    xmin, ymin, xmax, ymax = bbox

    # Build a low-resolution grid for IDW (50×50 cells), then resample to
    # raster resolution inside build_landscape_nc
    grid_res = 50
    grid_lats_1d = np.linspace(ymax, ymin, grid_res)
    grid_lons_1d = np.linspace(xmin, xmax, grid_res)
    grid_lons_2d, grid_lats_2d = np.meshgrid(grid_lons_1d, grid_lats_1d)

    logger.info(
        "[pipeline] %s | Running IDW on %d stations",
        datetime.now(timezone.utc).isoformat(),
        len(stations),
    )
    wind_u_grid, wind_v_grid = idw_wind_grid(stations, grid_lats_2d, grid_lons_2d)
    # Scalar wind speed for .ff script: use mean magnitude
    # avg_speed = float(np.mean(np.sqrt(wind_u_grid**2 + wind_v_grid**2)))

    # ------------------------------------------------------------------
    # 4. Build landscape.nc
    # ------------------------------------------------------------------
    sim_dir = os.path.join(config.simulations_dir, str(ocorrencia_id))
    os.makedirs(sim_dir, exist_ok=True)
    landscape_path = os.path.join(sim_dir, "landscape.nc")

    logger.info(
        "[pipeline] %s | Building landscape.nc at %s",
        datetime.now(timezone.utc).isoformat(),
        landscape_path,
    )
    build_landscape_nc(
        ocorrencia_id=ocorrencia_id,
        ignition_lat=ignition_lat,
        ignition_lon=ignition_lon,
        wind_u_grid=wind_u_grid,
        wind_v_grid=wind_v_grid,
        bbox=bbox,
        output_path=landscape_path,
    )
    logger.info("[pipeline] %s | landscape.nc written", datetime.now(timezone.utc).isoformat())

    # ------------------------------------------------------------------
    # 5. Write fuels.csv
    # ------------------------------------------------------------------
    fuels_path = os.path.join(sim_dir, "fuels.csv")
    logger.info("[pipeline] %s | Writing fuels.csv", datetime.now(timezone.utc).isoformat())
    write_fuels_csv(fuels_path)

    # ------------------------------------------------------------------
    # 6. Generate ForeFire script
    # ------------------------------------------------------------------
    logger.info("[pipeline] %s | Building ForeFire script", datetime.now(timezone.utc).isoformat())
    script_path = build_ff_script(
        ignition_lat=ignition_lat,
        ignition_lon=ignition_lon,
        bbox=bbox,
        sim_dir=sim_dir,
    )

    # ------------------------------------------------------------------
    # 7. Run ForeFire
    # ------------------------------------------------------------------
    logger.info("[pipeline] %s | Launching ForeFire subprocess", datetime.now(timezone.utc).isoformat())
    ff_result = run_forefire(sim_dir=sim_dir, script_path=script_path, timeout=600)

    if not ff_result["success"]:
        logger.error(
            "[pipeline] ForeFire failed (rc=%d): %s",
            ff_result["returncode"],
            ff_result["stderr"][:500],
        )
        return False

    logger.info("[pipeline] %s | ForeFire completed successfully", datetime.now(timezone.utc).isoformat())

    # ------------------------------------------------------------------
    # 8. Parse perimeters
    # ------------------------------------------------------------------
    logger.info("[pipeline] %s | Parsing perimeters from %s", datetime.now(timezone.utc).isoformat(), sim_dir)
    perimeters = parse_perimeters(sim_dir)
    logger.info("[pipeline] %s | Parsed %d perimeter features", datetime.now(timezone.utc).isoformat(), len(perimeters))

    # ------------------------------------------------------------------
    # 9. generate 3D models
    # ------------------------------------------------------------------
    export_perimeters_geojson_to_gltf(ignition_lat=ignition_lat, ignition_lon=ignition_lon, sim_id=ocorrencia_id)

    return True

def cleanup_simulation_files(ocorrencia_id: str) -> None:
    """Delete the simulation working directory and all its contents."""
    sim_dir = os.path.join(config.simulations_dir, str(ocorrencia_id))
    if os.path.exists(sim_dir):
        shutil.rmtree(sim_dir)
        logger.info("[pipeline] Cleaned up simulation files for %s", ocorrencia_id)
    else:
        logger.warning("[pipeline] No simulation files found to clean for %s", ocorrencia_id)

def process_ignition(message):
    topic_parts = message["topic"].split("/")
    thing_id = ":".join(topic_parts[:2])
    if topic_parts[-1] not in ("created", "deleted"):
        logger.warning(f"[ignition] Ignoring non-create/delete event for {thing_id}")
        return
    if message["path"] != "/":
        logging.warning(f"[ignition] not message from thing creation, ignoring {thing_id}")
        return
    if message["value"]["attributes"]["state"] != "new_ignition":
        logging.warning(f"[ignition] already processed thing, ignoring {thing_id}")
        return

    fire_ignition = message["value"]["attributes"]["fire_ignition"]

    logger.info(
        "[ignition] Received ignition at (%.5f, %.5f)",
        fire_ignition["lat"], fire_ignition["lon"])
        
    # 1. Generate cone segments
    cones, wind_direction, wind_speed = generate_cone_from_ignition(fire_ignition["lat"], fire_ignition["lon"])
    sim_dir = os.path.join(config.simulations_dir, str(thing_id))
    os.makedirs(sim_dir, exist_ok=True)
    save_cone(cones,sim_dir)
    export_cone_geojson_to_gltf(thing_id,fire_ignition["lat"],fire_ignition["lon"],cones)
    if not cones:
        logger.warning("[ignition] No cone generated for ignition at (%.5f, %.5f)", fire_ignition["lat"], fire_ignition["lon"])
        return {"message": "Ignition received but no cone could be generated due to lack of wind data."}
    
    # 2. Insert occurrence and cone into Ditto
    expire_min = datetime.now() + timedelta(minutes=10)
    urls = upload_simulation_dir_to_seaweed_minio(thing_id,type_send="cone")
    polygons = [urls["fire_cone.glb"]]
    cone = urls["cone_horizon.geojson"]
    thing_builder = DittoBodyBuilder(policy_id="fire:default",thing_id=thing_id, expiry_ts=expire_min).ignition(fire_ignition["lat"], fire_ignition["lon"]).cones(cone).fire_state(fireState.SIMULATING).polygon(polygons)
    incident = thing_builder.build()
    ditto_client.update_fire_incident(incident)
    
    # 2. Check intersection with risk areas
    risk_polygons = read_risky_areas_from_geojson(config.risk_areas_dir)
    intersects_risk = cone_intersects_polygons(cones, risk_polygons)
    if intersects_risk:
        logger.info("[ignition] Cone intersects with risk areas, launching pipeline")
        # 3. Launch pipeline in background
        expire_min = datetime.now() + timedelta(minutes=20)
        new_thing_builder = DittoBodyBuilder(policy_id="fire:default", thing_id=incident.thing_id, expiry_ts=expire_min).fire_state(fireState.SIMULATING)
        ditto_client.update_fire_incident(new_thing_builder.build())
        if(run_pipeline(incident.thing_id,fire_ignition["lat"],fire_ignition["lon"])):
            state = fireState.SIMULATED
        else:
            state = fireState.FAILED
    else:
        state = fireState.NO_RISK
        logger.info("[ignition] Cone does not intersect with any risk areas, no pipeline launched")
    
    #4 Upload files generated
    urls = urls | upload_simulation_dir_to_seaweed_minio(thing_id,type_send="perimeters")
    logger.debug(urls)
    polygons.append(urls["fire_simulation.glb"])
    perimeters: list[str] = []
    for front in FIRE_FRONTS:
        perimeters.append(urls[f"step_{front}.geojson"])
    #5 Update Ditto
    expire_min = datetime.now() + timedelta(minutes=10)
    thing_builder = DittoBodyBuilder(policy_id="fire:default",thing_id=thing_id,expiry_ts=expire_min)
    match state:
        case fireState.FAILED:
            thing_builder = thing_builder.fire_state(fireState.FAILED)
        case fireState.NO_RISK:
            thing_builder = thing_builder.fire_state(fireState.NO_RISK)
        case fireState.SIMULATED:
            thing_builder = thing_builder.fire_state(fireState.SIMULATED).polygon(polygons).perimeters(perimeters)
    
    ditto_client.update_fire_incident(thing_builder.build())

    # clean up files
    cleanup_simulation_files(thing_id)

    logger.debug(f"[URLS from S3] {urls}")
    logger.info("Files Send Pipeline Done")