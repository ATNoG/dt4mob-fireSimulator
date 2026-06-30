from boto3.s3.transfer import TransferConfig
from typing import Literal
import mimetypes
import os
import logging
from boto3 import client as boto3_client
from botocore.config import Config
from botocore.exceptions import ClientError
from settings import config


def upload_simulation_dir_to_seaweed_minio(simulationId: str,type_send: Literal["cone","perimeters"]) -> dict[str, str]:
    """
    Uploads specific files from a local simulation folder to SeaweedFS via boto3
    and returns a mapping of relative paths to their SeaweedFS URLs.
    :param simulationId: Simulation identifier (e.g., "fire:one")
    :return: A dictionary where key=relative_path and value=URL string
    """
    s3_vars = config.s3
    logger = logging.getLogger(__name__)

    s3_prefix = simulationId.replace(":", "_").strip("/")
    local_dir = os.path.join(config.simulations_dir, simulationId)
    endpoint_url = s3_vars.url_external
    bucket_name = s3_vars.bucket

    mimetypes.add_type('model/gltf-binary', '.glb')
    mimetypes.add_type('application/geo+json', '.geojson')

    uploaded_urls = {}

    if not os.path.isdir(local_dir):
        logger.error(f"Provided path is not a directory: {local_dir}")
        return uploaded_urls

    s3_config = Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        region_name="us-east-1",
        retries={
            "max_attempts": 3,
            "mode": "standard"
        }
    )
    s3 = boto3_client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=s3_vars.access_key,
        aws_secret_access_key=s3_vars.secret_key,
        config=s3_config
    )

    transfer_config = TransferConfig(
    multipart_threshold=10 * 1024 * 1024,  # 10MB (Don't chunk anything below this)
    multipart_chunksize=5 * 1024 * 1024,   # 5MB chunk sizes if it does cross the line
)

    base_endpoint = endpoint_url.rstrip("/")
    external_url = s3_vars.url_external.rstrip("/")

    allowed_files = ("cone_horizon.geojson","fire_cone.glb") if type_send == "cone" else ("step","fire_simulation")

    for root, _, files in os.walk(local_dir):
        for filename in files:
            if not filename.startswith(allowed_files):
                continue
            local_path = os.path.join(root, filename)
            relative_path = os.path.relpath(local_path, local_dir).replace("\\", "/")
            s3_key = f"{s3_prefix}/{relative_path}".replace("//", "/") if s3_prefix else relative_path

            content_type, _ = mimetypes.guess_type(local_path)
            if content_type is None:
                content_type = "application/octet-stream"

            logger.info(f"Uploading {local_path} -> {base_endpoint}/{bucket_name}/{s3_key} [{content_type}]")
            try:
                s3.upload_file(
                    Filename=local_path,
                    Bucket=bucket_name,
                    Key=s3_key,
                    ExtraArgs={"ContentType": content_type},
                    Config=transfer_config
                )
                
                uploaded_urls[filename] = f"{external_url}/{bucket_name}/{s3_key}"
            except ClientError as e:
                logger.error(f"Failed to upload {filename}: {e}")

    logger.info(f"Uploaded {len(uploaded_urls)} file(s) for simulation '{simulationId}'")
    return uploaded_urls