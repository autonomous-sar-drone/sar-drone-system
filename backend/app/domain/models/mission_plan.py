from dataclasses import dataclass

from app.domain.models.route import PlanningSettings, Route, RouteMetrics
from app.domain.models.search_area import SearchArea


@dataclass(frozen=True)
class MissionPlan:
    plan_id: str
    search_area: SearchArea
    planning_settings: PlanningSettings
    route: Route
    route_metrics: RouteMetrics