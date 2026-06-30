import enum
from quadkeys import get_tile_bounds

class Action(enum.Enum):
    UPDATE = "update"
    CREATE = "create"

def get_headers(action: Action) -> dict:
    if action == Action.UPDATE:
        return {"Content-Type": "application/merge-patch+json"}
    return {"Content-Type": "application/json"}

def prepare_search_params(lat: float, lon: float, zoom: int) -> dict:
    lower_qk, upper_qk = get_tile_bounds(lat, lon, zoom)
    return {
        "filter": (
            f"and("
            f"ge(attributes/geotile,{lower_qk}),le(attributes/geotile,{upper_qk}),"
            f"ne(features/meteorology/properties/wind_direction,9),"
            f"gt(features/meteorology/properties/wind_intensity,0),"
            f"exists(features/meteorology/properties/wind_intensity),"
            f"like(thingId,\"*meteo*\")"
            f")"
        )
    }