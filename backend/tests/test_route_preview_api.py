from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.route_planning import router as route_planning_router
from app.domain.models.route import PlanningSettings
from app.domain.models.telemetry import TelemetrySample
from app.mission.mission_service import MissionService
from app.runtime.telemetry_hub import TelemetryHub


VALID_REQUEST = {
    "searchArea": {
        "vertices": [
            {"latitudeDeg": 30.2672, "longitudeDeg": -97.7431},
            {"latitudeDeg": 30.2672, "longitudeDeg": -97.7426},
            {"latitudeDeg": 30.2676, "longitudeDeg": -97.7426},
            {"latitudeDeg": 30.2676, "longitudeDeg": -97.7431},
        ]
    }
}


def make_sample(age_s: float = 0.0) -> TelemetrySample:
    return TelemetrySample(
        sequence=1,
        timestamp_utc=datetime.now(timezone.utc) - timedelta(seconds=age_s),
        latitude_deg=30.2672,
        longitude_deg=-97.7431,
        relative_altitude_m=25.0,
        heading_deg=90.0,
        ground_speed_mps=4.0,
    )


def make_test_app(
    publish_telemetry: bool = True,
    max_route_distance_m: float = 10000.0,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub = TelemetryHub()

        if publish_telemetry:
            await hub.publish(make_sample())

        app.state.mission_service = MissionService(
            telemetry_hub=hub,
            default_planning_settings=PlanningSettings(
                search_altitude_m=25.0,
                lane_spacing_m=20.0,
            ),
            max_telemetry_age_s=3.0,
            max_transit_distance_m=2000.0,
            max_route_distance_m=max_route_distance_m,
        )

        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(route_planning_router)

    return app


def test_route_preview_returns_frontend_contract() -> None:
    app = make_test_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/routes/preview",
            json=VALID_REQUEST,
        )

    assert response.status_code == 200

    data = response.json()

    assert isinstance(data["planId"], str)
    assert data["planId"]
    assert len(data["route"]["waypoints"]) >= 2
    assert data["route"]["waypoints"][0]["altitudeReference"] == "HOME_RELATIVE"
    assert data["metrics"]["distanceToStartM"] >= 0.0
    assert data["metrics"]["routeDistanceM"] > 0.0
    assert data["planningUsed"] == {
        "searchAltitudeM": 25.0,
        "laneSpacingM": 20.0,
    }


def test_route_preview_accepts_planning_override() -> None:
    app = make_test_app()

    request = {
        **VALID_REQUEST,
        "planning": {
            "searchAltitudeM": 30.0,
            "laneSpacingM": 15.0,
        },
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/routes/preview",
            json=request,
        )

    assert response.status_code == 200

    data = response.json()

    assert data["planningUsed"] == {
        "searchAltitudeM": 30.0,
        "laneSpacingM": 15.0,
    }
    assert all(
        waypoint["altitudeM"] == 30.0
        for waypoint in data["route"]["waypoints"]
    )


def test_route_preview_returns_structured_invalid_area_error() -> None:
    app = make_test_app()

    request = {
        "searchArea": {
            "vertices": [
                {
                    "latitudeDeg": 30.2672,
                    "longitudeDeg": -97.7431,
                },
                {
                    "latitudeDeg": 30.2673,
                    "longitudeDeg": -97.7430,
                },
            ]
        }
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/routes/preview",
            json=request,
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "INVALID_SEARCH_AREA",
            "message": "The search area geometry is invalid.",
            "details": {
                "reason": "TOO_FEW_VERTICES",
            },
        }
    }


def test_route_preview_returns_vehicle_position_unavailable() -> None:
    app = make_test_app(
        publish_telemetry=False
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/routes/preview",
            json=VALID_REQUEST,
        )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "VEHICLE_POSITION_UNAVAILABLE",
        "message": "Current vehicle position is unavailable.",
        "details": {
            "reason": "NO_TELEMETRY",
        },
    }


def test_route_preview_returns_route_too_long() -> None:
    app = make_test_app(
        max_route_distance_m=1.0
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/routes/preview",
            json=VALID_REQUEST,
        )

    assert response.status_code == 422

    detail = response.json()["detail"]

    assert detail["code"] == "ROUTE_TOO_LONG"
    assert detail["details"]["routeDistanceM"] > 1.0
    assert detail["details"]["maxRouteDistanceM"] == 1.0


def test_route_preview_rejects_nonpositive_planning_values() -> None:
    app = make_test_app()

    request = {
        **VALID_REQUEST,
        "planning": {
            "searchAltitudeM": 0.0,
            "laneSpacingM": -1.0,
        },
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/routes/preview",
            json=request,
        )

    # Pydantic catches malformed request values before Mission receives them.
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)