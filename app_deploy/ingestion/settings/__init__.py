from settings.s3 import s3Settings
import logging
from pydantic_settings import SettingsConfigDict
from settings.auth import AuthSettings
from settings.ditto import DittoSettings
from typing import Literal
from pydantic_settings import BaseSettings

LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]

class Config(BaseSettings):
    log_level: LogLevel = "INFO"

    simulations_dir: str = "/data/simulations"
    risk_areas_dir: str = "/data/raw/brisa_zones.geojson"
    forefire_bin: str = "/usr/local/bin/forefire"

    ditto: DittoSettings = DittoSettings.model_construct()
    auth: AuthSettings = AuthSettings.model_construct()
    s3: s3Settings = s3Settings.model_construct()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_nested_delimiter="__",
    )



config = Config()

logging.basicConfig(level=config.log_level)
logging.debug(config)