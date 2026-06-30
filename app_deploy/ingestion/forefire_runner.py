"""
forefire_runner.py — Subprocess wrapper for the ForeFire simulation engine.

The ForeFire binary is built into the ingestion image (multi-stage Dockerfile).
Simulations run as subprocesses inside the ingestion container, writing output
to /data/simulations/{sim_id}/ on the shared volume.
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from settings import config

from pyproj import Transformer

logger = logging.getLogger(__name__)

_to_metric = Transformer.from_crs("EPSG:4326", "EPSG:3763", always_xy=True)


_STEP_DT   = 900   # 15 minutes per step
_NUM_STEPS = 8     # 8 × 15 min = 2 h horizon


def _step_filename(step_min: int) -> str:
    return f"step_{step_min:04d}.geojson"


def build_ff_script(
    ignition_lat: float,
    ignition_lon: float,
    bbox: tuple[float, float, float, float],
    sim_dir: str,
) -> str:
    """
    Write a ForeFire .ff script that prints a perimeter every 15 min.

    Output files: step_0015.geojson, step_0030.geojson … step_0120.geojson
    """
    xmin, ymin, xmax, ymax = bbox

    sw_x, sw_y = _to_metric.transform(xmin, ymin)
    ne_x, ne_y = _to_metric.transform(xmax, ymax)
    lx = abs(ne_x - sw_x)
    ly = abs(ne_y - sw_y)

    logger.info(
        "[forefire] Domain SW=(%.1f, %.1f) m, Lx=%.1f m, Ly=%.1f m",
        sw_x, sw_y, lx, ly,
    )

    os.makedirs(sim_dir, exist_ok=True)

    landscape_path = os.path.join(sim_dir, "landscape.nc")
    fuels_path     = os.path.join(sim_dir, "fuels.csv")
    script_path    = os.path.join(sim_dir, "forefire_script.ff")
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build one goTo+print block per 15-min step
    steps_block = ""
    for i in range(1, _NUM_STEPS + 1):
        t_s   = i * _STEP_DT
        t_min = i * (_STEP_DT // 60)
        steps_block += f"goTo[t={t_s}]\nprint[{_step_filename(t_min)}]\n"

    script = f"""\
# ForeFire simulation — DT4MOB
# {_NUM_STEPS} steps × {_STEP_DT // 60} min = {_NUM_STEPS * _STEP_DT // 60} min horizon
setParameter[fuelsTableFile={fuels_path}]
setParameter[propagationModel=Rothermel]
setParameter[minimalPropagativeFrontDepth=20]
setParameter[perimeterResolution=10]
setParameter[spatialIncrement=3]
setParameter[relax=0.5]
setParameter[windReductionFactor=0.4]
setParameter[minSpeed=0.009]
setParameter[dumpMode=geojson]
setParameter[ForeFireDataDirectory={sim_dir}]
loadData[{landscape_path};{now_ts}]
startFire[lonlat=({ignition_lon:.6f},{ignition_lat:.6f},0.);t=0]
{steps_block}"""

    with open(script_path, "w", encoding="utf-8") as fh:
        fh.write(script)

    logger.info("[forefire] Script written to %s (%d steps)", script_path, _NUM_STEPS)
    return script_path


def run_forefire(
    sim_dir: str,
    script_path: str,
    timeout: int = 600,
) -> dict:
    """
    Execute the ForeFire binary with the given script as a subprocess.

    Returns
    -------
    dict: success (bool), stdout (str), stderr (str), returncode (int)
    """
    cmd = [config.forefire_bin, "-i", script_path]
    logger.info(
        "[forefire] Running: %s  (cwd=%s, timeout=%ds)", " ".join(cmd), sim_dir, timeout
    )

    try:
        result = subprocess.run(
            cmd,
            cwd=sim_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("[forefire] Process timed out after %d s", timeout)
        return {"success": False, "stdout": exc.stdout or "", "stderr": exc.stderr or "", "returncode": -1}
    except FileNotFoundError:
        msg = f"ForeFire binary not found: '{config.forefire_bin}'. Check forefire_bin env var."
        logger.error("[forefire] %s", msg)
        return {"success": False, "stdout": "", "stderr": msg, "returncode": -2}
    except OSError as exc:
        logger.error("[forefire] OS error: %s", exc)
        return {"success": False, "stdout": "", "stderr": str(exc), "returncode": -3}

    for line in (result.stdout or "").splitlines():
        logger.info("[forefire stdout] %s", line)
    for line in (result.stderr or "").splitlines():
        logger.warning("[forefire stderr] %s", line)

    success = result.returncode == 0
    logger.info("[forefire] Finished returncode=%d (success=%s)", result.returncode, success)
    return {
        "success": success,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }


def perimeters_output_path(sim_dir: str) -> str:
    """Return path of the last expected step file (used to check completion)."""
    return os.path.join(sim_dir, _step_filename(_NUM_STEPS * (_STEP_DT // 60)))


def parse_perimeters(sim_dir: str) -> list[dict]:
    """
    Read all step_NNNN.geojson files from sim_dir.

    Returns a list of dicts with keys: timestep_min (int), geojson (dict).
    """
    perimeters = []
    for i in range(1, _NUM_STEPS + 1):
        t_min = i * (_STEP_DT // 60)
        path = os.path.join(sim_dir, _step_filename(t_min))
        if not os.path.exists(path):
            logger.warning("[forefire] Step file missing: %s", path)
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[forefire] Could not parse %s: %s", path, exc)
            continue

        features = data.get("features", [])
        if not features:
            logger.info("[forefire] %s: no features (fire may not have spread yet)", path)
            continue

        # Take the first (and typically only) feature from each step file
        feat = features[0]
        perimeters.append({"timestep_min": t_min, "geojson": feat})
        logger.info("[forefire] Step %d min: %d feature(s)", t_min, len(features))

    logger.info("[forefire] Parsed %d timestep perimeters total", len(perimeters))
    return perimeters
