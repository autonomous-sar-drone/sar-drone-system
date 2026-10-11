from enum import StrEnum


class MissionPlanningErrorCode(StrEnum):
    INVALID_SEARCH_AREA = "INVALID_SEARCH_AREA"
    VEHICLE_POSITION_UNAVAILABLE = "VEHICLE_POSITION_UNAVAILABLE"
    SEARCH_AREA_OUT_OF_RANGE = "SEARCH_AREA_OUT_OF_RANGE"
    ROUTE_TOO_LONG = "ROUTE_TOO_LONG"
    MISSION_ACTION_NOT_ALLOWED = "MISSION_ACTION_NOT_ALLOWED"


class MissionPlanningError(Exception):
    def __init__(
        self,
        code: MissionPlanningErrorCode,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details