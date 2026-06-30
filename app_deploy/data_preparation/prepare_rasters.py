"""
DT4MOB — prepare_rasters.py
Pre-processes terrain rasters from the source DEM and COSc land cover raster.

Run once after dropping source files into their data directories:
    docker compose exec ingestion python prepare_rasters.py

Outputs to data/processed/ (all Cloud-Optimized GeoTIFF, EPSG:4326):
    dem.tif        — merged/reprojected DEM
    slope.tif      — slope in degrees (0–90)
    aspect.tif     — aspect in degrees (0–360, N=0, flat=0)
    fuel_type.tif  — COSc class codes, Int16, aligned to DEM grid
                     (non-burnable codes 100/500/620 remapped to 0)

The COSc pixel values are used directly as ForeFire fuel type indices
(they match the Index column in fuels.csv). No reclassification to IDs 1-12.

After this script completes, run:
    docker compose exec postgis psql -U dt4mob -d dt4mob -f /scripts/05_register_rasters.sql
"""
from botocore import exceptions
import time
import zipfile
from dotenv import load_dotenv
import shutil
import boto3

import logging
import os
import subprocess
import sys
from pathlib import Path
import requests

import numpy as np
import rasterio
from osgeo import gdal

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [prepare_rasters] %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Paths (inside container) ─────────────────────────────────────────────────
DATA_DEM = Path(os.environ.get("DATA_DEM", "/data/dem"))
DATA_COS = Path(os.environ.get("DATA_COS", "/data/cos"))
DATA_OUT = Path(os.environ.get("DATA_PROCESSED", "/data/processed"))
BRIZA_ZONES_PATH = os.environ.get("BRIZA_ZONES_PATH","/data/raw/brisa_zones.geojson")

DEM_OUT       = DATA_OUT / "dem.tif"
SLOPE_OUT     = DATA_OUT / "slope.tif"
ASPECT_OUT    = DATA_OUT / "aspect.tif"
FUEL_TYPE_OUT = DATA_OUT / "fuel_type.tif"

# COSc codes that map to non-burnable (ForeFire stops spread at pixel value 0)
NON_BURNABLE_CODES = {100, 500, 620}  # Artificializado, Sem Vegetação, Água

COG_CREATION_OPTIONS = [
    "COMPRESS=DEFLATE",
    "TILED=YES",
    "BLOCKXSIZE=512",
    "BLOCKYSIZE=512",
    "COPY_SRC_OVERVIEWS=YES",
    "BIGTIFF=YES",
]
DEM_URL = os.environ.get("DEM_URL","")
COS_URL = os.environ.get("COS_URL","")
S3_ENDPOINT = os.getenv("S3_ENDPOINT")
BUCKET_NAME = os.getenv("S3_BUCKET")
FILE_KEY = os.getenv("RISK_AREAS_FILE_KEY")
AWS_ACCESS_KEY_ID=os.getenv("AWS_ACCESS_KEY_ID"),
AWS_SECRET_ACCESS_KEY=os.getenv("AWS_SECRET_ACCESS_KEY")

def download_file(url: str, folder, new_filename: str):
    """
    Downloads a file from a URL and saves it to a folder with a new name.
    """
    # 1. Ensure the directory exists
    downloaded = 0
    last_reported = 0
    response = requests.get(url, stream=True)
    total_size = int(response.headers.get('content-length', 0))
    os.makedirs(folder, exist_ok=True)
    
    # 2. Build the full destination path
    final_tif_path = os.path.join(folder, new_filename)
    temp_zip_path = os.path.join(folder, "temp_download.zip")
    save_path = temp_zip_path if url.endswith(".zip") else final_tif_path
    
    with open(save_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=1024 * 1024): # 1MB chunks
            if chunk:
                file.write(chunk)
            downloaded += len(chunk)
            percentage: int | float = (downloaded / total_size) * 100
            if percentage >= last_reported + 10:
                log.info(f" Download: {percentage:.0f}% complete")
                last_reported = int(percentage)
            
    
    if url.endswith(".zip"):
        log.info("[pipeline] Extracting .tif from zip")
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            # Find the .tif file inside the zip
            tif_files = [f for f in zip_ref.namelist() if f.endswith('.tif')]
            if not tif_files:
                raise FileNotFoundError("No .tif file found in the zip archive.")
            
            # Extract the first one found
            target_file = tif_files[0]
            with zip_ref.open(target_file) as source, open(final_tif_path, 'wb') as target:
                shutil.copyfileobj(source, target)
        os.remove(temp_zip_path)
                
    log.info(f"File successfully saved to: {final_tif_path}")



def _run(cmd: list[str], desc: str) -> None:
    log.info("%s: %s", desc, " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("%s failed:\n%s", desc, result.stderr)
        raise RuntimeError(f"{desc} failed")
    if result.stdout.strip():
        log.debug(result.stdout.strip())


def _to_cog(src: Path, dst: Path) -> None:
    """Convert any GeoTIFF to a Cloud-Optimized GeoTIFF."""
    gdal.UseExceptions()
    translate_opts = gdal.TranslateOptions(
        format="GTiff",
        creationOptions=COG_CREATION_OPTIONS,
    )
    gdal.Translate(str(dst), str(src), options=translate_opts)
    log.info("COG written: %s", dst)


# ── Step 1: reproject DEM → EPSG:4326, write as COG ─────────────────────────
def _is_epsg4326(path: Path) -> bool:
    """Return True if the raster is already in EPSG:4326."""
    from osgeo import osr
    ds = gdal.Open(str(path))
    if ds is None:
        return False
    wkt = ds.GetProjection()
    ds = None
    if not wkt:
        return False
    srs = osr.SpatialReference()
    srs.ImportFromWkt(wkt)
    srs.AutoIdentifyEPSG()
    return srs.GetAuthorityCode(None) == "4326"


def build_dem() -> None:
    download_file(DEM_URL,DATA_DEM,"dem.tif")
    tifs = sorted(DATA_DEM.glob("*.tif"))
    if not tifs:
        log.error("No .tif files found in %s — drop the DEM there first.", DATA_DEM)
        sys.exit(1)

    log.info("Found %d DEM file(s) in %s", len(tifs), DATA_DEM)

    # gdalbuildvrt handles both single files and multiple tiles transparently
    vrt = DATA_OUT / "_dem_merge.vrt"
    _run(
        ["gdalbuildvrt", str(vrt)] + [str(t) for t in tifs],
        "gdalbuildvrt",
    )

    tmp = DATA_OUT / "_dem_warp_tmp.tif"

    if _is_epsg4326(tifs[0]):
        log.info("DEM is already EPSG:4326 — skipping reprojection")
        # gdalbuildvrt output still needs to become a real GeoTIFF for _to_cog
        _run(
            ["gdal_translate", "-of", "GTiff","-co", "BIGTIFF=YES", str(vrt), str(tmp)],
            "gdal_translate (VRT → GeoTIFF)",
        )
    else:
        log.info("Reprojecting DEM → EPSG:4326 new")
        _run(
            [
                "gdalwarp",
                "-t_srs", "EPSG:4326",
                "-r", "bilinear",
                "-of", "GTiff",
                "-co", "COMPRESS=DEFLATE",
                "-co", "BIGTIFF=YES",
                "-co", "PREDICTOR=2",
                str(vrt),
                str(tmp),
            ],
            "gdalwarp (reproject DEM → EPSG:4326)",
        )

    vrt.unlink(missing_ok=True)
    _to_cog(tmp, DEM_OUT)
    tmp.unlink(missing_ok=True)
    log.info("DEM ready: %s", DEM_OUT)


# ── Step 4: fuel_type raster from COSc source raster ─────────────────────────
def build_fuel_type() -> None:
    """
    Convert the COSc 2025 raster to a ForeFire fuel type raster.

    COSc pixel values (211, 312, 410, etc.) are used directly as ForeFire
    fuel type indices — they match the Index column in fuels.csv verbatim.
    Non-burnable codes (100, 500, 620) are remapped to 0.

    The raster is warped to match the DEM grid exactly (same bbox, resolution,
    EPSG:4326). Output dtype is Int16 to hold 3-digit COSc codes.
    """
    download_file(COS_URL,DATA_COS,"cos.tif")
    gdal.UseExceptions()

    # Find COSc source raster
    cos_files = (
        sorted(DATA_COS.glob("*.tif"))
        + sorted(DATA_COS.glob("*.img"))
        + sorted(DATA_COS.glob("*.vrt"))
    )
    if not cos_files:
        log.error(
            "No raster files (.tif / .img / .vrt) found in %s — "
            "drop the COSc raster there first.",
            DATA_COS,
        )
        sys.exit(1)
    cos_src = cos_files[0]
    log.info("Using COSc raster: %s", cos_src)

    # Read DEM grid parameters for alignment
    dem_ds = gdal.Open(str(DEM_OUT))
    gt = dem_ds.GetGeoTransform()   # (xmin, xres, 0, ymax, 0, -yres)
    xsize = dem_ds.RasterXSize
    ysize = dem_ds.RasterYSize
    xmin = gt[0]
    ymax = gt[3]
    xres = gt[1]
    yres = abs(gt[5])
    xmax = xmin + xsize * xres
    ymin = ymax - ysize * yres
    dem_ds = None

    log.info(
        "DEM grid: %dx%d pixels, %.6f° resolution, bbox [%.4f, %.4f, %.4f, %.4f]",
        xsize, ysize, xres, xmin, ymin, xmax, ymax,
    )

    # Warp COSc raster to DEM grid — nearest neighbour preserves integer class codes
    tmp_warped = DATA_OUT / "_cosc_warped_tmp.tif"
    _run(
        [
            "gdalwarp",
            "-t_srs", "EPSG:4326",
            "-r", "near",
            "-ot", "Int16",
            "-of", "GTiff",
            "-te", str(xmin), str(ymin), str(xmax), str(ymax),
            "-tr", str(xres), str(yres),
            "-dstnodata", "0",
            "-co", "BIGTIFF=YES",
            str(cos_src),
            str(tmp_warped),
        ],
        "gdalwarp (reproject COSc raster to DEM grid)",
    )

    # Remap non-burnable codes to 0 (ForeFire stops spread at fuelType=0)
    tmp_remapped = DATA_OUT / "_cosc_remapped_tmp.tif"
    with rasterio.open(str(tmp_warped)) as src:
        data = src.read(1).astype(np.int16)
        profile = src.profile.copy()
        profile.update(dtype="int16", nodata=0, bigtiff="YES")
        for code in NON_BURNABLE_CODES:
            data[data == code] = 0
        with rasterio.open(str(tmp_remapped), "w", **profile) as dst:
            dst.write(data, 1)

    tmp_warped.unlink(missing_ok=True)
    _to_cog(tmp_remapped, FUEL_TYPE_OUT)
    tmp_remapped.unlink(missing_ok=True)
    log.info("Fuel type raster ready: %s", FUEL_TYPE_OUT)

def obtain_risk_areas():
    # 1. Gather configuration from environment variables
    s3_endpoint = os.getenv("S3_ENDPOINT")
    bucket_name = os.getenv("S3_BUCKET")
    file_key = os.getenv("RISK_AREAS_FILE_KEY") # e.g., 'risk_areas.geojson'

    if not all([s3_endpoint, bucket_name, file_key]):
        log.warning("S3 configuration missing — skipping risk areas download")
        return

    log.info("Initializing S3 client to download risk areas from SeaweedFS...")
    
    # Initialize the S3 client using credentials automatically injected by K8s
    s3_client = boto3.client(
        's3',
        endpoint_url=s3_endpoint,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
    )

    log.info("Starting risk areas S3 download loop...")
    
    while True:
        try:
            # 2. Download the file directly to your local file path
            s3_client.download_file(bucket_name, file_key, BRIZA_ZONES_PATH)
            
            log.info("Risk areas GeoJSON successfully downloaded from S3 to: %s", BRIZA_ZONES_PATH)
            break  # Exit the retry loop upon successful download
                
        except exceptions.ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == "404":
                log.error("File '%s' not found in bucket '%s'. Retrying in 10 seconds...", file_key, bucket_name)
            elif error_code == "403":
                log.error("Access Denied! Verify your S3Policy permissions. Retrying in 10 seconds...")
            else:
                log.error("S3 ClientError (%s): %s. Retrying in 10 seconds...", error_code, e)
                
        except Exception as e:
            log.error("Unexpected error connecting to SeaweedFS S3: %s. Retrying in 10 seconds...", e)

        time.sleep(10)


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    done_file = os.path.join(DATA_OUT,".done")
    DATA_OUT.mkdir(parents=True, exist_ok=True)
    # file done exists, skip processing
    if os.path.exists(done_file):
        log.info("Rasters already prepared in %s — skipping", DATA_OUT)
        sys.exit(0)

    log.info("=== prepare_rasters.py start ===")

    log.info("Step 1/2 — DEM")
    build_dem()

    log.info("Step 2/2 — Fuel type (from COSc raster in %s)", DATA_COS)
    build_fuel_type()

    log.info("=== All rasters ready in %s ===", DATA_OUT)
    
    gdal.GDALDestroyDriverManager()
    with open(done_file,'w') as fp:
        fp.write("done")
    sys.exit(0)

if __name__ == "__main__":
    main()
