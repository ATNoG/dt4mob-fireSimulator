from typing import Self
import uuid
from datetime import datetime
from pydantic import BaseModel
from models.fire_incident import fireIncidentThing, fireState, fireIncidentThingAttributes, fireIncidentThingFeatures, ConeProperties, PerimetersProperties, Point,  ConeFeature, PerimetersFeature

class Perimeter(BaseModel):
    points: list[Point]
    horizonte_min: int


class DittoBodyBuilder:
    def __init__(self, thing_id: str | None = None, expiry_ts: datetime | None = None):
        self.thing_id = thing_id if thing_id is not None else "fire:incident_" + uuid.uuid4().hex
        self.ignition_point: Point | None = None
        self.state: fireState | None = None
        self.cone_properties: ConeProperties | None = None
        self.perimeters_properties: PerimetersProperties | None = None
        self.polygon_url: list[str] | None = None
        self.expiry_ts: datetime | None = expiry_ts

    def polygon(self,polygon: list[str]) -> Self:
        self.polygon_url = polygon
        return self

    def ignition(self, lat: float, lon: float) -> Self:
        self.ignition_point = Point(lat=lat, lon=lon)
        return self

    def cones(self, url) -> Self:
        self.cone_properties = ConeProperties(perimeters=url)
        return self

    def perimeters(self,perimeters: list[str]) -> Self:
        self.perimeters_properties = PerimetersProperties(perimeters=perimeters)
        return self

    def fire_state(self, state: fireState) -> Self:
        self.state = state
        return self
    
    def _features(self) -> fireIncidentThingFeatures | None:
        cone_feature = ConeFeature(properties=self.cone_properties) if self.cone_properties else None
        perimeters_feature = PerimetersFeature(properties=self.perimeters_properties) if self.perimeters_properties else None
        if not cone_feature and not perimeters_feature:
            return None
        return fireIncidentThingFeatures(cone=cone_feature, perimeters=perimeters_feature)
    
    def _attributes(self) -> fireIncidentThingAttributes | None:
        if self.ignition_point is None and self.state is None and self.polygon_url is None and self.expiry_ts is None:
            return None
        return fireIncidentThingAttributes(fire_ignition=self.ignition_point, state=self.state, polygon=self.polygon_url, expiry_ts=self.expiry_ts)


    def build(self) -> fireIncidentThing:
        attributes = self._attributes()        
        features = self._features()
        thing = fireIncidentThing(thing_id=self.thing_id, attributes=attributes, features=features)
        return thing
    
