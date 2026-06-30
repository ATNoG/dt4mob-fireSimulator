from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from typing import Annotated
import enum

class Point(BaseModel):
    lat: float
    lon: float

class fireState(enum.Enum):
    NEW_IGNITION = "new_ignition"
    NO_RISK = "no_risk"
    SIMULATING = "simulating"
    SIMULATED = "simulated"
    FAILED = "simulation_failed"

class ConeSection(BaseModel):
    points: list[Point]
    horizonte_min: int

# ————————————————————————————
# Cone properties
# ————————————————————————————
class ConeProperties(BaseModel):
    perimeters: str | None = None

class ConeFeature(BaseModel):
    properties: ConeProperties 

# ————————————————————————————
# Perimeters properties
# ————————————————————————————
class PerimetersProperties(BaseModel):
    perimeters: list[str] | None = None

class PerimetersFeature(BaseModel):
    properties: PerimetersProperties

# ————————————————————————————
# Fire incident thing
# ————————————————————————————
class fireIncidentThingAttributes(BaseModel):
    fire_ignition: Point | None = None
    state: fireState | None = None
    polygon: list[str] | None = None
    expiry_ts: datetime | None = None

class fireIncidentThingFeatures(BaseModel):
    cone: ConeFeature | None = None
    perimeters: PerimetersFeature | None = None

class fireIncidentThing(BaseModel):
    thing_id: Annotated[str, Field(alias="thingId",exclude=True)]
    policy_id: Annotated[str,Field(alias="policyId")]
    attributes: fireIncidentThingAttributes | None = None
    features: fireIncidentThingFeatures | None = None

    model_config = ConfigDict(populate_by_name=True)