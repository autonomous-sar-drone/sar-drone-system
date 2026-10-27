from enum import StrEnum


# planning failures are separate from problems controlling an active mission
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


# these codes are also exposed in HTTP responses and WebSocket runtime errors
class MissionExecutionErrorCode(StrEnum):
    MISSION_ACTION_NOT_ALLOWED = "MISSION_ACTION_NOT_ALLOWED"
    PLAN_NOT_CURRENT = "PLAN_NOT_CURRENT"
    EXECUTION_NOT_CURRENT = "EXECUTION_NOT_CURRENT"
    VEHICLE_POSITION_UNAVAILABLE = "VEHICLE_POSITION_UNAVAILABLE"
    VEHICLE_NOT_READY = "VEHICLE_NOT_READY"
    VEHICLE_COMMAND_FAILED = "VEHICLE_COMMAND_FAILED"
    MISSION_MONITOR_FAILED = "MISSION_MONITOR_FAILED"


# details can carry things like a missing heartbeat or unexpected disarm
class MissionExecutionError(Exception):
    def __init__(
        self,
        code: MissionExecutionErrorCode,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
