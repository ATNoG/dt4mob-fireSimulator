# DT4MOB — User Manual

Digital Twin for Climate-Resilient Highway Management.
Wildfire spread forecasting for Brisa highway corridors in Portugal.

## Overview

The system integrates real-time wind data with the ForeFire fire propagation engine to:

1. Accept ignition points (from a map UI or Ditto events)
2. Compute a quick propagation cone based on wind from the nearest weather station
3. Check if the cone intersects a Brisa highway zone
4. If it does, run a full ForeFire simulation producing 8 perimeters (15-min intervals, 2-hour horizon)
5. Upload results (GeoJSON + 3D GLTF models) to S3-compatible storage
6. Update the digital twin state in Eclipse Ditto

## Architecture

```
IPMA Weather Stations (Ditto Things)    Map UI / External System
              │                                      │
              ▼                                      ▼
        ┌─────────────────────────────────────────────────┐
        │          INGESTION SERVICE (Python)              │
        │  • Listens for Ditto WebSocket events            │
        │  • Searches nearest stations via Ditto API       │
        │  • IDW wind interpolation                        │
        │  • Builds landscape.nc + fuels.csv               │
        │  • Runs ForeFire subprocess                      │
        │  • Uploads results to S3 + updates Ditto         │
        └────────────┬────────────────────┬────────────────┘
                     │ subprocess         │ HTTP / S3
                     ▼                    ▼
        ┌────────────────────┐  ┌────────────────────┐
        │  FOREFIRE ENGINE   │  │  S3 (SeaweedFS)    │
        │  (C++ / Rothermel) │  │  • GeoJSON files   │
        │  • landscape.nc    │  │  • GLTF 3D models  │
        │  • step_*.geojson  │  │  • Cone data       │
        └────────────────────┘  └────────────────────┘
                     │
                     ▼
        ┌────────────────────┐
        │  ECLIPSE DITTO     │
        │  • Fire incidents  │
        │  • Weather stations│
        │  • Spatial search  │
        └────────────────────┘
```

**Key services the system depends on (not included in this repo):**
- **Eclipse Ditto** — digital twin platform (WebSocket events, HTTP API)
- **Keycloak** — OAuth2/OIDC authentication
- **SeaweedFS** (or any S3-compatible store, e.g. MinIO) — file storage

---

## Prerequisites

- Docker Compose v2 (`docker compose`) or Podman + podman-compose
- The following data files must be in place:

| File | Location | Description |
|------|----------|-------------|
| DEM | `app_deploy/data/dem/dem.tif` | DGT LiDAR digital elevation model (any CRS, auto-reprojected) |
| COSc land cover | `app_deploy/data/cos/` | COSc 2025 raster (.tif), downloaded automatically if URLs configured |
| Brisa zones | `app_deploy/data/raw/brisa_zones.geojson` | Brisa highway concession polygons, EPSG:4326 |

**Podman users:** alias `docker` and `docker compose` to their Podman equivalents:
```bash
alias docker=podman
alias docker compose='podman compose'
```
Add to `~/.bashrc` or `~/.zshrc` to make permanent.

---

## Configuration

Copy the example environment file:

```bash
cp app_deploy/.env.example app_deploy/.env
```

Edit `.env` with your credentials:

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `DEBUG` | Log level |
| `RISKY_AREAS_PATH` | `/data/raw/brisa_zones.geojson` | Path to Brisa zones GeoJSON file |
| `FOREFIRE_BIN` | `/usr/local/bin/forefire` | ForeFire binary path (built into container) |
| `SIM_BASE_DIR` | `/data/simulations` | Simulation working directory |
| `DITTO__API_URL` | `https://localhost:8080` | Ditto API URL |
| `DITTO__BASE_API_PATH` | `/api/2` | Ditto API base path |
| `DITTO__WS__PATH` | `/ws/2` | Ditto WebSocket base path |
| `AUTH__TOKEN_ENDPOINT` | `https://localhost:8080/auth/realms/dt4mob/protocol/openid-connect/token` | Keycloak token endpoint |
| `AUTH__CLIENT_ID` | `ditto` | Keycloak client ID |
| `AUTH__USERNAME` | — | Ditto username |
| `AUTH__PASSWORD` | — | Ditto password |
| `S3__URL_INTERNAL` | `http://localhost:8333/` | Internal S3 endpoint URL |
| `S3__URL_EXTERNAL` | `http://localhost:8333/` | External S3 endpoint URL |
| `S3__BUCKET` | `test-bucket` | S3 bucket name |
| `S3__ACCESS_KEY` | — | S3 access key |
| `S3__SECRET_KEY` | — | S3 secret key |
| `COS_URL` | `https://geo2.dgterritorio.gov.pt/cosc/COSc2025.zip` | COSc 2025 download URL |
| `DEM_URL` | `http://gis.ciimar.up.pt/gis-data/lidar-dgt/MDT-10m-PT.tif` | DEM download URL |

---

## Deploy with Docker Compose

### 1. Build and start

```bash
cd app_deploy
docker compose up -d --build
```

This builds all images and starts the ingestion service.

### 2. Prepare terrain rasters

```bash
docker compose exec ingestion python prepare_rasters.py
```

Downloads DEM and COSc (if URLs configured), reprojects to EPSG:4326, produces Cloud-Optimized GeoTIFFs in `data/processed/`:
- `dem.tif` — elevation
- `fuel_type.tif` — COSc fuel codes (non-burnable remapped to 0)

### 3. Verify the service

```bash
curl -s http://localhost:8000/api/health          # {"status": "ok"}
curl -s http://localhost:8000/api/wind | python3 -m json.tool  # wind observations
```

### 4. Access endpoints

| Service | URL | Description |
|---------|-----|-------------|
| Ingestion API | `http://localhost:8000` | FastAPI application (Swagger at `/docs`) |
| ForeFire Web UI | `http://localhost:8001` | ForeFire built-in interface (if enabled) |
| Frontend Map | `http://localhost:8080` | Leaflet map (if enabled) |

---

## Deploy with Helm (Kubernetes)

The repository includes a Helm chart at `chart/` for Kubernetes deployment.

### Prerequisites

- Kubernetes cluster (v1.19+)
- **SeaweedFS operator** installed with a running SeaweedFS cluster
- **Eclipse Ditto** deployed and accessible
- **Keycloak** deployed and accessible
- PersistentVolume provisioner (e.g. `local-path`, `longhorn`, or `ebs`)

### Chart structure

```
chart/
├── Chart.yaml                # dt4mob-fire-simulator v0.1.0
├── values.yaml               # All configurable parameters
└── templates/
    ├── api-deployment.yaml   # Ingestion service Deployment
    ├── api-service.yaml      # Service exposing port 8080
    ├── prepare-job.yaml      # One-shot raster preparation Job
    ├── pvc.yaml              # 20Gi PersistentVolumeClaim
    ├── secret.yaml           # Ditto credentials Secret
    ├── s3-identity-creds.yaml# SeaweedFS S3Identity + S3Credentials
    ├── s3-policy-app.yaml    # S3 policy + binding for ingestion
    ├── s3-policy-job.yaml    # S3 policy + binding for raster job
    └── _helpers.tpl          # Template helpers
```

### Key values.yaml settings

```yaml
images:
  service:
    repository: atnog-harbor.av.it.pt/dt4mob/dt4mob-fire-simulator
    tag: latest
  rasterJob:
    repository: atnog-harbor.av.it.pt/dt4mob/dt4mob-raster-prepare-fire-simulator
    tag: latest

service:
  config:
    riskyAreasPath: "/data/raw/brisa_zones.geojson"
    forefireBin: "/usr/local/bin/forefire"
    ditto:
      apiUrl: "http://dt4mob-ditto-gateway:8080"
    s3:
      UrlInternal: "http://seaweed-s3.seaweedfs.svc.cluster.local:8333"
      UrlExternal: "http://localhost:8333"
    secret:
      # existingSecret: 
      userName: "user"
      password: "password"
    auth:
      url: "https://dt4mob-keycloak:8443/auth/realms/dt4mob/protocol/openid-connect/token"
      clientId: "ditto"

storage:
  className: local-path
  size: 20Gi
```

### Deploy

```bash
helm install dt4mob . -f my-values.yaml
```

### What gets deployed

| Resource | Description |
|----------|-------------|
| **Deployment** | Ingestion API container (with init container waiting for rasters) |
| **Service** | Exposes port 8080 within the cluster |
| **Job** | `prepare_rasters.py` — downloads DEM + COSc, produces COG files, writes `.done` marker |
| **PVC** | 20Gi shared volume for rasters + simulation data |
| **Secret** | Ditto credentials (username/password) |
| **S3Identity** | SeaweedFS user for S3 access |
| **S3Credentials** | Auto-generated S3 access/secret keys |
| **S3Policy + Binding** | IAM-style policies for bucket access (app + job) |

The init container in the deployment waits for the `data/processed/.done` file before the main container starts, ensuring rasters are ready before processing ignitions.

---

## Using the Application

### Placing an ignition

1. Open the map UI (or send a Ditto thing with `state: "new_ignition"`)
2. The system fetches wind from the nearest weather station via Ditto spatial search
3. A propagation cone is generated (±15° spread, 4 × 30-min horizons)
4. The cone is checked against Brisa highway zones

### If cone intersects a Brisa zone

The full ForeFire pipeline runs automatically:

1. Weather stations are queried from Ditto (nearest 5)
2. IDW interpolation produces wind u/v grids
3. `landscape.nc` is built from DEM + fuel COG rasters (reprojected to EPSG:3763)
4. `fuels.csv` is written with Rothermel parameters
5. A ForeFire `.ff` script is generated
6. ForeFire runs as a subprocess (8 steps × 15 min = 2 h horizon)
7. Step perimeters are parsed from `step_NNNN.geojson` files
8. 3D GLTF models are generated from perimeters and cone
9. All files are uploaded to S3
10. The Ditto thing is updated with results

### States

| State | Meaning |
|-------|---------|
| `new_ignition` | Ignition event received, processing |
| `no_risk` | Cone does not intersect any highway zone |
| `simulating` | ForeFire simulation in progress |
| `simulated` | Simulation completed, results available |
| `simulation_failed` | Pipeline encountered an error |

### Output files

Each simulation writes to `data/simulations/{thing_id}/`:

| File | Description |
|------|-------------|
| `landscape.nc` | NetCDF terrain + fuel + wind grid |
| `fuels.csv` | Rothermel fuel model parameters |
| `forefire_script.ff` | ForeFire command script |
| `cone_horizon.geojson` | Quick-propagation cone |
| `step_0015.geojson` ... `step_0120.geojson` | 8 perimeter timesteps |
| `fire_cone.glb` | 3D cone model |
| `fire_simulation.glb` | 3D perimeters model |

---

## Troubleshooting

**Simulation stays in `simulating` forever**
Check the ingestion logs:
```bash
docker compose logs -f ingestion
```
Common causes:
- Wind data unavailable (no Ditto stations found)
- ForeFire binary not found (check `FOREFIRE_BIN`)
- Raster files missing (run `docker compose exec ingestion python prepare_rasters.py` first)

**Raster preparation fails**
Verify DEM and COSc files are in the correct directories and are valid:
```bash
docker compose exec ingestion gdalinfo data/dem/dem.tif
```

**No wind data returned**
Ditto must have weather station things with meteorology features:
```bash
curl -s http://localhost:8000/api/wind | python3 -m json.tool
```
If empty, check Ditto connectivity and station data availability.

**S3 uploads fail**
Verify S3 credentials and endpoint URL. The system uses boto3 with path-style addressing.

---

## Data Layout

```
app_deploy/data/
├── dem/                  # DEM source rasters (drop .tif files here)
├── cos/                  # COSc land-cover source rasters
├── raw/
│   └── brisa_zones.geojson   # Brisa highway polygons
├── processed/            # COG rasters (generated, gitignored)
│   ├── dem.tif
│   ├── slope.tif
│   ├── aspect.tif
│   └── fuel_type.tif
└── simulations/          # Per-run ForeFire files (gitignored)
    └── {thing_id}/
        ├── landscape.nc
        ├── fuels.csv
        ├── forefire_script.ff
        ├── cone_horizon.geojson
        ├── step_0015.geojson ... step_0120.geojson
        ├── fire_cone.glb
        └── fire_simulation.glb
```
