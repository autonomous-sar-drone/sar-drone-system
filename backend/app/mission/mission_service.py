import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.domain.models.mission_plan import MissionPlan
from app.domain.models.mission_state import MissionState
from app.domain.models.route import PlanningSettings
from app.domain.models.search_area import GeoPoint, SearchArea
from app.geospatial.route_planning import generate_route
from app.geospatial.search_area_validation import validate_search_area
from app.mission.errors import MissionPlanningError, MissionPlanningErrorCode
from app.runtime.telemetry_hub import TelemetryHub


class MissionService:
    def __init__(
        self,
        telemetry_hub: TelemetryHub,
        default_planning_settings: PlanningSettings,
        max_telemetry_age_s: float,
        max_transit_distance_m: float,
        max_route_distance_m: float,
    ) -> None:
        self._telemetry_hub = telemetry_hub
        self._default_planning_settings = default_planning_settings
        self._max_telemetry_age_s = max_telemetry_age_s
        self._max_transit_distance_m = max_transit_distance_m
        self._max_route_distance_m = max_route_distance_m

        self._state = MissionState.IDLE
        self._proposed_plan: MissionPlan | None = None
        self._planning_lock = asyncio.Lock()

    @property
    def state(self) -> MissionState:
        return self._state

    @property
    def proposed_plan(self) -> MissionPlan | None:
        return self._proposed_plan

    async def preview_route(
        self,
        search_area: SearchArea,
        planning_settings: PlanningSettings | None = None,
    ) -> MissionPlan:
        # Only one preview can replace the proposed plan at a time.
        async with self._planning_lock:
            if self._state not in {MissionState.IDLE, MissionState.PLANNING}:
                raise MissionPlanningError(
                    code=MissionPlanningErrorCode.MISSION_ACTION_NOT_ALLOWED,
                    message="A route preview cannot be generated in the current mission state.",
                    details={"current_state": self._state.value},
                )

            # A new preview attempt makes any older proposed plan stale.
            self._state = MissionState.PLANNING
            self._proposed_plan = None

            validation = validate_search_area(search_area)
            if not validation.valid:
                raise MissionPlanningError(
                    code=MissionPlanningErrorCode.INVALID_SEARCH_AREA,
                    message="The search area geometry is invalid.",
                    details={
                        "reason": validation.reason.value
                        if validation.reason
                        else None
                    },
                )

            _, telemetry = await self._telemetry_hub.get_latest()

            if telemetry is None:
                raise MissionPlanningError(
                    code=MissionPlanningErrorCode.VEHICLE_POSITION_UNAVAILABLE,
                    message="Current vehicle position is unavailable.",
                    details={"reason": "NO_TELEMETRY"},
                )

            telemetry_age_s = (
                datetime.now(timezone.utc) - telemetry.timestamp_utc
            ).total_seconds()

            if telemetry_age_s > self._max_telemetry_age_s:
                raise MissionPlanningError(
                    code=MissionPlanningErrorCode.VEHICLE_POSITION_UNAVAILABLE,
                    message="Current vehicle position is unavailable.",
                    details={
                        "reason": "STALE_TELEMETRY",
                        "telemetry_age_seconds": telemetry_age_s,
                        "max_telemetry_age_seconds": self._max_telemetry_age_s,
                    },
                )

            start_reference = GeoPoint(
                latitude_deg=telemetry.latitude_deg,
                longitude_deg=telemetry.longitude_deg,
            )
            settings = planning_settings or self._default_planning_settings

            route, metrics = generate_route(
                search_area=search_area,
                planning_settings=settings,
                start_reference=start_reference,
            )

            if metrics.distance_to_start_m > self._max_transit_distance_m:
                raise MissionPlanningError(
                    code=MissionPlanningErrorCode.SEARCH_AREA_OUT_OF_RANGE,
                    message="The search area is too far from the current vehicle position.",
                    details={
                        "distance_to_start_m": metrics.distance_to_start_m,
                        "max_transit_distance_m": self._max_transit_distance_m,
                    },
                )

            if metrics.route_distance_m > self._max_route_distance_m:
                raise MissionPlanningError(
                    code=MissionPlanningErrorCode.ROUTE_TOO_LONG,
                    message="The generated route is too long for the configured mission limits.",
                    details={
                        "route_distance_m": metrics.route_distance_m,
                        "max_route_distance_m": self._max_route_distance_m,
                    },
                )

            plan = MissionPlan(
                plan_id=str(uuid4()),
                search_area=search_area,
                planning_settings=settings,
                route=route,
                route_metrics=metrics,
            )

            self._proposed_plan = plan

            return plan