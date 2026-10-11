from enum import StrEnum


class MissionState(StrEnum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    PREFLIGHT_CHECK = "PREFLIGHT_CHECK"
    RUNNING = "RUNNING"