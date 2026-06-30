# DT4MOB — Developer Manual

Digital Twin for Climate-Resilient Highway Management.
Wildfire spread forecasting for Brisa highway corridors in Portugal.

---

## Technologies

### Languages

| Language | Where | Purpose |
|----------|-------|---------|
| Python 3.11 | `ingestion/`, `data_preparation/` | Main application logic, pipeline, raster processing |
| C++ | ForeFire engine (external) | Fire propagation simulation (Rothermel model) |
| YAML | `docker-compose.yml`, Helm `chart/` | Container orchestration, Kubernetes deployment |
| HTML/JavaScript | Frontend (external, not in repo) | Leaflet map UI |

### Key Tools

| Tool | Purpose |
|------|---------|
| Docker / Podman | Containerization |
| Docker Compose | Local development orchestration |
| Helm | Kubernetes package management |
| Eclipse Ditto | Digital twin platform (WebSocket events + HTTP API) |
| Keycloak | OAuth2/OIDC authentication |
| SeaweedFS / MinIO | S3-compatible object storage |
| GDAL | Geospatial raster processing |

### External Services (not in repo)

- **Eclipse Ditto** — receives `new_ignition` events via WebSocket, stores weather station things, provides spatial search API
- **Keycloak** — issues JWT tokens for Ditto authentication
- **SeaweedFS** (or MinIO) — stores simulation output files (GeoJSON, GLTF)

---

## Python Packages

All 16 dependencies in `ingestion/requirements.txt`:

| Package | Version | Purpose |
|---------|---------|---------|
| `requests` | 2.31.0 | HTTP requests (data downloads) |
| `numpy` | 1.26.4 | Numerical arrays, grid operations |
| `netCDF4` | 1.6.5 | NetCDF file I/O for `landscape.nc` |
| `rasterio` | 1.3.10 | GeoTIFF reading, reprojection, writing |
| `pyproj` | 3.6.1 | Coordinate reference system transforms |
| `shapely` | 2.0.4 | Geometric operations (intersection, polygon) |
| `python-dotenv` | 1.0.1 | `.env` file loading |
| `GDAL` | 3.6.4 | Geospatial raster processing (warp, slope, aspect) |
| `trimesh` | 4.12.2 | 3D mesh generation and GLTF export |
| `scipy` | 1.17.1 | Delaunay triangulation, IDW interpolation |
| `mapbox_earcut` | 2.0.0 | Polygon triangulation (used by trimesh) |
| `websockets` | 16.0 | WebSocket client for Ditto event streaming |
| `pydantic` | 2.13.3 | Data validation models |
| `pydantic-settings` | 2.14.1 | Settings management from env vars |
| `httpx` | 0.28.1 | HTTP client (async + sync, used for Ditto API) |
| `boto3` | 1.43.10 | S3-compatible storage client |

---

## Project Structure

```
forefire_brisa_edited/
├── README_USER.md                  # User manual (deploy & use)
├── README_DEV.md                   # This file
├── .gitignore
│
├── app_deploy/
│   ├── .env.example                # Environment variable template
│   ├── docker-compose.yml          # Docker Compose services
│   │
│   ├── ingestion/                  # ★ Main application
│   │   ├── Dockerfile              # Multi-stage: ForeFire C++ build + Python runtime
│   │   ├── requirements.txt        # Python dependencies
│   │   ├── main.py                 # Entry point — async Ditto WebSocket worker
│   │   ├── pipeline.py             # Simulation pipeline orchestrator
│   │   ├── wind.py                 # IPMA wind data, Ditto spatial search, IDW interpolation
│   │   ├── landscape.py            # Builds landscape.nc (NetCDF) + fuels.csv
│   │   ├── forefire_runner.py      # .ff script generator + subprocess wrapper
│   │   ├── cone_management.py      # Stage-1 quick cone geometry
│   │   ├── geojson.py              # GeoJSON I/O (Brisa zones, cone saves)
│   │   ├── s3_uploader.py          # S3 (SeaweedFS) file uploader
│   │   ├── gltf_experter.py        # 3D GLTF mesh export
│   │   ├── quadkeys.py             # QuadTile computation for Ditto spatial search
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── constants.py        # Fire front timesteps, colors
│   │   │   ├── fire_incident.py    # Pydantic models for Ditto things
│   │   │   └── ditto_body_maker.py # Builder pattern for Ditto thing payloads
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── auth/
│   │   │   │   └── __init__.py     # Keycloak OAuth2 authentication
│   │   │   └── ditto/
│   │   │       ├── __init__.py     # DittoClient singleton
│   │   │       ├── ditto_class.py  # WebSocket + HTTP Ditto client
│   │   │       └── utils.py        # Search/filter helpers, headers
│   │   └── settings/
│   │       ├── __init__.py         # Config class (pydantic-settings)
│   │       ├── auth.py             # Auth settings (Keycloak token endpoint)
│   │       ├── ditto.py            # Ditto connection settings (API URL, WS path)
│   │       └── s3.py              # S3 settings (endpoint, bucket, credentials)
│   │
│   ├── data_preparation/
│   │   ├── Dockerfile              # Python image for raster prep
│   │   ├── requirements.txt        # Subset of deps (rasterio, GDAL, boto3)
│   │   └── prepare_rasters.py      # DEM + COSc → COG raster pipeline
│   │
│   ├── forefire/
│   │   ├── Dockerfile              # Standalone ForeFire web UI container
│   │   └── start.ff                # ForeFire listen script
│   │
│   └── data/
│       ├── dem/                    # DEM source rasters
│       ├── cos/                    # COSc source rasters
│       ├── raw/
│       │   ├── brisa_zones.geojson # Brisa highway polygons
│       │   └── helper_pod.yaml     # K8s utility pod
│       ├── processed/              # COG output rasters (gitignored)
│       └── simulations/            # Per-run ForeFire files (gitignored)
│
└── chart/                          # Helm chart for Kubernetes
    ├── Chart.yaml
    ├── values.yaml
    └── templates/
        ├── _helpers.tpl
        ├── api-deployment.yaml
        ├── api-service.yaml
        ├── prepare-job.yaml
        ├── pvc.yaml
        ├── secret.yaml
        ├── s3-bucket.yaml
        ├── s3-identity-creds.yaml
        ├── s3-policy-app.yaml
        └── s3-policy-job.yaml
```

---

## Module Deep-Dive

### `main.py` — Entry Point

An async worker that connects to Eclipse Ditto via WebSocket and listens for `new_ignition` events:

```python
await ditto_client.connect("fire", process_ignition)
```

The `process_ignition` callback is invoked for each event. The worker handles graceful shutdown via `SIGINT`/`SIGTERM` signal handlers.

### `pipeline.py` — Pipeline Orchestrator

Two functions:

- **`process_ignition(message)`** — called for each Ditto event. Generates the quick cone, checks Brisa zone intersection via local GeoJSON, uploads cone to S3, updates Ditto thing state, and conditionally launches `run_pipeline()`.

- **`run_pipeline(ocorrencia_id, lat, lon)`** — executes the full ForeFire simulation:
  1. Queries nearest 5 weather stations from Ditto spatial search
  2. Computes IDW wind interpolation on a 50×50 grid
  3. Builds `landscape.nc` from COG rasters (reprojected to EPSG:3763)
  4. Writes `fuels.csv` with hardcoded Rothermel parameters
  5. Generates ForeFire `.ff` script
  6. Runs ForeFire as subprocess (10-minute timeout)
  7. Parses perimeter GeoJSON output (8 files, 15-min steps)
  8. Exports 3D GLTF models (cone + perimeters)
  9. Uploads all files to S3
  10. Updates Ditto thing with result URLs and final state

State machine: `new_ignition` → `simulating` → `simulated` / `simulation_failed` / `no_risk`

### `wind.py` — Wind Data

- **`DIR_MAP`**: IPMA direction code (1-9) → degrees clockwise from North
- **`_dir_code_to_uv(intensity, code)`**: converts IPMA "wind FROM" direction to meteorological u/v "wind TOWARDS" components
- **`get_nearest_stations(conn, lat, lon, n)`**: queries Ditto spatial search using QuadTile indexing, expanding the zoom level until `n` stations are found. Filters for stations with valid wind data (`wind_direction != 9`, `wind_intensity > 0`).
- **`idw_wind_grid(stations, grid_lats, grid_lons, power=2)`**: Inverse Distance Weighting interpolation using geodesic distances (pyproj Geod). For single-station case, fills entire grid with constant value.

### `landscape.py` — NetCDF + CSV Builder

- **`build_landscape_nc()`**: reads COG rasters (`dem.tif`, `fuel_type.tif`) from `/data/processed/`, reprojects to EPSG:3763 metric coordinates, clips to simulation bounding box, and writes NetCDF.
- **`_read_reprojected()`**: extracts a bbox from a GeoTIFF and reprojects on-the-fly to EPSG:3763 at a specified resolution.
- **`write_fuels_csv()`**: writes a hardcoded Rothermel parameter table matching COSc 2025 codes (13 fuel types including non-burnable ID 0).

Raster arrays are flipped vertically (`np.flipud`) because `rasterio` stores row-0 at north, while ForeFire/NetCDF convention expects row-0 at south.

### `forefire_runner.py` — ForeFire Wrapper

- **`build_ff_script()`**: generates a `.ff` script with:
  - Fuel table reference, Rothermel propagation model
  - Simulation parameters (resolution, wind reduction, minimum speed)
  - `loadData` for `landscape.nc`
  - `startFire` with ignition lon/lat
  - 8 × `goTo[t=900]` + `print[step_NNNN.geojson]` blocks (15-min intervals, 2 h total)
- **`run_forefire(sim_dir, script_path, timeout=600)`**: runs `forefire -i script.ff` as subprocess with error handling (timeout, binary not found, OS errors).
- **`parse_perimeters(sim_dir)`**: reads all 8 `step_NNNN.geojson` files, returns list of `{timestep_min, geojson}` dicts.

### `cone_management.py` — Quick Cone

- **`generate_cone_from_ignition(lat, lng)`**: fetches nearest station, computes spread rate (`0.18 × wind_speed km/h`), generates 4 cone segments at 30/60/90/120 min horizons. The cone spans ±15° around the downwind direction (`wind_direction + 180°`).
- **`cone_intersects_polygons(segments, risk_polygons)`**: checks Shapely intersection between any cone segment and any Brisa zone polygon.
- **`_destination_point(lat, lng, bearing, distance_km)`**: haversine formula for point-in-bearing calculations.

### `geojson.py` — Local GeoJSON I/O

- **`read_risky_areas_from_geojson(file_path)`**: loads Brisa highway zones from a local GeoJSON file into Shapely polygons.
- **`save_cone(cones, base_path)`**: writes the generated cone as a GeoJSON FeatureCollection to disk.

### `s3_uploader.py` — S3 Upload

- **`upload_simulation_dir_to_seaweed_minio(simulationId, type_send)`**: uploads files to S3-compatible storage via boto3. Two modes:
  - `"cone"`: uploads `cone_horizon.geojson` and `fire_cone.glb`
  - `"perimeters"`: uploads `step_*.geojson` files and `fire_simulation.glb`
- Returns a dict mapping filenames to their S3 URLs.
- Uses path-style addressing, s3v4 signatures, and multipart transfer config.

### `gltf_experter.py` — 3D GLTF Export

- **`export_perimeters_geojson_to_gltf()`**: reads perimeters from GeoJSON, samples elevation from `landscape.nc` via `RectBivariateSpline`, triangulates with Delaunay, builds a `trimesh.Scene` with color-coded fire fronts (green→red ramp).
- **`export_cone_geojson_to_gltf()`**: same approach for the cone geometry.
- Each front/cone segment gets a unique color from `FIREFRONT_COLORS`.
- An ignition dot (blue sphere) is placed at the origin.
- Output: `fire_simulation.glb` and `fire_cone.glb`.

### `quadkeys.py` — QuadTile Indexing

Implements the QuadTile system used by Eclipse Ditto for spatial indexing. Functions:

- **`get_quadkey(lat, lng, zoom)`**: encodes lat/lon into a 64-bit integer quadkey (max zoom 31).
- **`get_tile_bounds(lat, lng, tile_zoom, max_zoom=31)`**: computes lower and upper quadkey bounds for a tile at the given zoom level, used in Ditto filter queries.

### `models/fire_incident.py` — Pydantic Models

- `Point` — lat/lon pair
- `fireState` — enum: `NEW_IGNITION`, `NO_RISK`, `SIMULATING`, `SIMULATED`, `FAILED`
- `ConeSection` — polygon points + horizon minutes
- `fireIncidentThing` — Ditto thing structure with `thingId`, `policyId`, `attributes` (ignition point, state, polygon URLs, expiry), and `features` (cone perimeter URL, perimeters list)

### `models/ditto_body_maker.py` — Builder Pattern

`DittoBodyBuilder` implements a fluent builder interface for constructing Ditto thing payloads:

```python
thing = (DittoBodyBuilder(policy_id="fire:default", thing_id=thing_id)
         .ignition(lat, lon)
         .cones(cone_url)
         .fire_state(fireState.SIMULATING)
         .polygon(polygon_urls)
         .perimeters(perimeter_urls)
         .build())
```

### `services/ditto/ditto_class.py` — Ditto Client

`DittoClient` manages:
- **WebSocket connection** to Ditto for event streaming
- **Token refresh loop** — periodically refreshes Keycloak JWT and pushes it to Ditto via `JWT-TOKEN` control message
- **Event listen loop** — receives WebSocket messages, dispatches to `process_ignition()` callback
- **HTTP methods**:
  - `update_fire_incident()` — PATCH request to update thing state
  - `get_stations_zoom_sync()` — GET request to search/things with QuadTile filter

Control messages:
- `START-SEND-EVENTS?namespaces=fire&filter=eq(attributes/state,'new_ignition')` — subscribes to ignition events
- `JWT-TOKEN?jwtToken=...` — refreshes WebSocket authorization

### `services/ditto/utils.py` — Ditto Helpers

- `prepare_search_params()`: builds Ditto filter strings using QuadTile bounds to search for weather station things within a geographic area.
- `get_headers()`: returns appropriate Content-Type headers (`application/merge-patch+json` for updates, `application/json` for creates).

### `services/auth/__init__.py` — Keycloak Auth

`AuthenticationService` manages OAuth2 password grant flow:
- Requests JWT tokens from Keycloak
- Caches tokens and refreshes at half their expiry time
- Supports file-based credentials for Kubernetes secrets (via `AUTH_USERNAME_FILE` / `AUTH_PASSWORD_FILE`)
- Supports custom CA certificates for TLS connections

### `settings/` — Configuration

Uses `pydantic-settings` with `env_nested_delimiter="__"` for nested env vars:

| Env var | Setting | Default |
|---------|---------|---------|
| `LOG_LEVEL` | `log_level` | `DEBUG` |
| `RISKY_AREAS_PATH` | `risk_areas_dir` | `/data/raw/brisa_zones.geojson` |
| `FOREFIRE_BIN` | `forefire_bin` | `/usr/local/bin/forefire` |
| `SIM_BASE_DIR` | `simulations_dir` | `/data/simulations` |
| `DITTO__API_URL` | `ditto.api_url` | `https://localhost:8080` |
| `DITTO__BASE_API_PATH` | `ditto.base_api_path` | `/api/2` |
| `DITTO__WS__PATH` | `ditto.base_ws_path` | `/ws/2` |
| `AUTH__TOKEN_ENDPOINT` | `auth.token_endpoint` | `https://localhost:8080/auth/realms/dt4mob/protocol/openid-connect/token` |
| `AUTH__CLIENT_ID` | `auth.client_id` | `ditto` |
| `AUTH__USERNAME` | `auth.username` | — |
| `AUTH__PASSWORD` | `auth.password` | — |
| `AUTH__USERNAME_FILE` | `auth.username_file` | — |
| `AUTH__PASSWORD_FILE` | `auth.password_file` | — |
| `S3__URL_INTERNAL` | `s3.url_internal` | `http://localhost:8333/` |
| `S3__URL_EXTERNAL` | `s3.url_external` | `http://localhost:8333/` |
| `S3__BUCKET` | `s3.bucket` | `test-bucket` |
| `S3__ACCESS_KEY` | `s3.access_key` | — |
| `S3__SECRET_KEY` | `s3.secret_key` | — |
| `COS_URL` | — (used in `prepare_rasters.py`) | `https://geo2.dgterritorio.gov.pt/cosc/COSc2025.zip` |
| `DEM_URL` | — (used in `prepare_rasters.py`) | `http://gis.ciimar.up.pt/gis-data/lidar-dgt/MDT-10m-PT.tif` |

### `data_preparation/prepare_rasters.py` — Raster Preprocessing

One-off script that:
1. Downloads DEM from URL (optional) or uses file in `data/dem/`
2. Downloads COSc 2025 from URL (optional) or uses file in `data/cos/`
3. Reprojects DEM to EPSG:4326 via `gdalwarp`
4. Generates `slope.tif` and `aspect.tif` via `gdaldem`
5. Warps COSc raster to DEM grid with nearest-neighbour resampling
6. Remaps non-burnable COSc codes (100, 500, 620) to 0
7. Writes all outputs as Cloud-Optimized GeoTIFFs to `data/processed/`
8. Creates `.done` marker file

In K8s context, also downloads Brisa zones GeoJSON from S3 if configured.

---

## Two-Stage Workflow

### Stage 1 — Quick Cone

Triggered by a `new_ignition` Ditto event:

1. Nearest weather station is fetched from Ditto spatial search (QuadTile-based)
2. Wind direction/speed determine cone geometry:
   - Center bearing = wind direction + 180° (downwind)
   - Spread rate = `0.18 × wind_speed` km/h
   - 4 cone segments at 30/60/90/120 min, ±15° spread
3. Cone is saved to local disk as GeoJSON
4. 3D GLTF cone model is generated
5. Cone + GLTF are uploaded to S3
6. Ditto thing is updated with `state: simulating` + cone URLs
7. Brisa zones are loaded from local GeoJSON
8. `shapely.intersects()` checks cone-vs-zone intersection

### Stage 2 — Full ForeFire Simulation

Triggered only if cone intersects a Brisa zone:

1. Ditto thing updated to `state: simulating`
2. Nearest 5 stations fetched, IDW wind grid computed
3. `landscape.nc` built from COG rasters (altitude @ 100 m, fuel @ 10 m resolution)
4. `fuels.csv` written (13 fuel types, COSc codes as indices)
5. `.ff` script generated with 8 steps × 15 min = 2 h total
6. `forefire` binary run as subprocess
7. 8 perimeter GeoJSON files parsed
8. 3D GLTF model generated from perimeters
9. All files uploaded to S3 (cone + perimeters)
10. Ditto thing updated with `state: simulated` + all URLs

---

## Wind Processing

### IPMA Direction Convention

IPMA `idDireccVento` is the direction **from** which the wind blows (meteorological convention). The system converts to u/v components:

```python
DIR_MAP = {1: 0, 2: 45, 3: 90, 4: 135, 5: 180, 6: 225, 7: 270, 8: 315, 9: 0}
# code 1 = North (from North), code 7 = West (from West)

u = -speed * sin(radians(direction))  # eastward (negated = towards)
v = -speed * cos(radians(direction))  # northward (negated = towards)
```

Example: code 7 (from West, 270°) → wind blowing eastward → `u = +speed`, `v ≈ 0`.

### IDW Interpolation

Inverse Distance Weighting is computed using geodesic distances (WGS84 ellipsoid via pyproj `Geod`):

```python
dist_m = _geod.inv(lon1, lat1, lon2, lat2)  # returns meters
weight = 1.0 / dist_m**power  # power=2
```

The interpolated u/v values at each grid cell are the weighted average of all station values, normalized by the sum of weights.

---

## NetCDF Format (`landscape.nc`)

ForeFire uses a **local metric coordinate system** (SW corner = origin). Geographic reference is via `BBoxWSEN`.

### Dimensions

| Dimension | Size | Description |
|-----------|------|-------------|
| `ny`, `nx` | varies | Altitude grid (100 m resolution) |
| `fy`, `fx` | varies | Fuel grid (10 m resolution) |
| `wind_rows`, `wind_columns` | 50×50 | IDW wind grid resolution |
| `nt`, `nz`, `ft`, `fz` | 1 | Time and z-level (singleton) |

### Variables

| Variable | Shape | Dtype | Description |
|----------|-------|-------|-------------|
| `domain` | scalar | str | Metadata with spatial reference attributes |
| `altitude` | (1, 1, ny, nx) | int16 | Elevation in metres, row 0 = south |
| `fuel` | (1, 1, fy, fx) | int16 | Fuel type ID (0 = non-burnable), row 0 = south |
| `wind` | (2, 2, wr, wc) | float32 | [u/v, timestep, rows, cols]; u = eastward m/s |
| `lat`, `lon` | (ny,), (nx,) | float64 | Latitude/longitude coordinates for altitude grid |
| `fuel_lat`, `fuel_lon` | (fy,), (fx,) | float64 | Latitude/longitude for fuel grid |

### Domain Attributes

| Attribute | Value |
|-----------|-------|
| `SWx`, `SWy` | 0.0 (local origin at SW corner) |
| `Lx`, `Ly` | Domain width/height in metres (EPSG:3763) |
| `BBoxWSEN` | `"xmin,ymin,xmax,ymax"` in WGS84 degrees |
| `t0` | 0.0 |
| `Lt` | inf |
| `WSENLBRT` | Combined WGS84 + local bounds string |

### Coordinate Conventions

- Row 0 = south (NetCDF/ForeFire convention). `rasterio` reads north-at-row-0, so `np.flipud()` is applied.
- Wind is constant across both timesteps (same u/v for t=0 and t=1).
- ForeFire derives slope and aspect from `altitude` internally.

---

## ForeFire Integration

### Script Format

```
# ForeFire simulation — DT4MOB
setParameter[fuelsTableFile=/data/simulations/{id}/fuels.csv]
setParameter[propagationModel=Rothermel]
setParameter[minimalPropagativeFrontDepth=20]
setParameter[perimeterResolution=10]
setParameter[spatialIncrement=3]
setParameter[relax=0.5]
setParameter[windReductionFactor=0.4]
setParameter[minSpeed=0.009]
setParameter[dumpMode=geojson]
setParameter[ForeFireDataDirectory=/data/simulations/{id}]
loadData[/data/simulations/{id}/landscape.nc;2024-01-01T12:00:00Z]
startFire[lonlat=(-8.324000,41.209000,0.);t=0]
goTo[t=900]
print[step_0015.geojson]
goTo[t=1800]
print[step_0030.geojson]
...
goTo[t=7200]
print[step_0120.geojson]
```

8 steps × 15 min = 2-hour horizon. The ForeFire repo is at https://github.com/forefireAPI/forefire (GPL-3.0).

### Simulation Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `propagationModel` | `Rothermel` | Rothermel ROS model |
| `minimalPropagativeFrontDepth` | 20 | Minimum fire front depth (m) |
| `perimeterResolution` | 10 | Perimeter vertex spacing (m) |
| `windReductionFactor` | 0.4 | Wind sheltering factor |
| `minSpeed` | 0.009 | Minimum spread rate (m/s) |

### Fuel Model Mapping (COSc 2025 → Rothermel)

Fuel IDs in `fuels.csv` match COSc pixel values directly (not renumbered):

| ID | COSc Code | Name | Fire Behavior | Load (t/ha) | SAV (1/m) |
|----|-----------|------|---------------|-------------|-----------|
| 0 | 100, 500, 620 | Non-burnable | Stop | 0.0 | 0 |
| 211 | 211 | Anuais Seco | Fast fine fuel | 1.8 | 3500 |
| 212 | 212 | Anuais Verde | Slow/green | 1.2 | 3000 |
| 213 | 213 | Outras Agricolas | Mixed pasture | 2.5 | 2800 |
| 311 | 311 | Sobreiro/Azinheira | Low intensity | 5.5 | 1800 |
| 312 | 312 | **Eucalipto** | **High/Spotting** | **18.5** | **5000** |
| 313 | 313 | Outras Folhosas | Hardwood litter | 6.0 | 2200 |
| 321 | 321 | Pinheiro Bravo | Conifer litter | 8.5 | 1500 |
| 322 | 322 | Pinheiro Manso | Clean understory | 5.0 | 1400 |
| 323 | 323 | Outras Resinosas | Dense conifer | 7.5 | 1600 |
| 410 | 410 | Matos | High intensity shrubs | 13.0 | 2000 |
| 420 | 420 | Herbacea Espontanea | Fast grass | 2.0 | 3500 |
| 610 | 610 | Zonas Humidas | Moist marsh, slow | 1.5 | 2500 |

ID 0 is critical — ForeFire treats `fuel=0` as an immediate spread stop (urban firebreaks, rivers, bare rock).

---

## Containerization

### Multi-Stage Dockerfile (`ingestion/Dockerfile`)

**Stage 1 — forefire-builder (Ubuntu 22.04)**
- Clones `forefireAPI/forefire` from GitHub
- Builds with cmake
- Installs binary to `/forefire/install/bin/`

**Stage 2 — Python runtime (python:3.11-slim)**
- System deps: GDAL, NetCDF libraries, build tools
- Copies ForeFire binary from builder stage
- Installs Python requirements
- Entry: `python main.py`

### Docker Compose (`docker-compose.yml`)

Currently only the `ingestion` service is active:
- Uses `network_mode: host` for direct Ditto access
- Mounts `./data:/data:z` (shared volume with SELinux context)
- Reads credentials from `.env` file

Additional services (commented out): `prepare_rasters_job`, `forefire` standalone UI.

### Helm Chart (`chart/`)

| Template | Kind | Purpose |
|----------|------|---------|
| `api-deployment.yaml` | Deployment | Ingestion service; init container waits for rasters |
| `api-service.yaml` | Service | Exposes port 8080 |
| `prepare-job.yaml` | Job | Runs `prepare_rasters.py` once |
| `pvc.yaml` | PersistentVolumeClaim | 20Gi for raster + simulation data |
| `secret.yaml` | Secret | Ditto credentials |
| `s3-identity-creds.yaml` | S3Identity + S3Credentials | SeaweedFS IAM user |
| `s3-policy-app.yaml` | S3Policy + S3PolicyBinding | Bucket access for ingestion |
| `s3-policy-job.yaml` | S3Policy + S3PolicyBinding | Bucket access for raster job |

Key Kubernetes features:
- Init container waits for `/data/processed/.done` file (raster preparation marker)
- Credentials from Kubernetes Secrets mounted at `/run/secrets/dt4mob/`
- TLS certificates from Kubernetes Secrets for Keycloak HTTPS
- Resource limits: 3Gi request / 8Gi limit for raster job; 20Gi PVC

---

## License

All custom code: to be defined by Brisa / project team.
ForeFire engine: GPL-3.0 (https://github.com/forefireAPI/forefire).
