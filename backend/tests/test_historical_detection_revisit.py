"""Backend safety/route contract for historical camera-frame visits."""
import asyncio
import math
import time
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.api.schemas.detections import candidate_view
from app.domain.models.detection import CaptureViewpoint, PixelBoundingBox
from app.domain.models.mission_state import FlightActivity, HoldReason, MissionState
from app.mission.errors import MissionExecutionError
from app.vision.candidate_store import CandidateStore
from test_terrain_mission_flow import create_running, sample, wait_until


def candidate(store, execution_id, grid, north=24, west=-10, altitude=20, *, status=None, yaw=1.1):
    lat, lon = grid.local_to_geo(north, west)
    now = datetime.now(timezone.utc)
    record = store.publish(
        first_observed_at=now, captured_at=now,
        confidence=.86, bounding_box=PixelBoundingBox(10, 10, 20, 40, 200, 150),
        estimated_location=None, snapshot_jpeg=b'\xff\xd8viewpoint\xff\xd9',
        capture_viewpoint=CaptureViewpoint(lat, lon, altitude, yaw),
        execution_id=execution_id,
    )
    if status == 'DISMISSED':
        record = store.review(record.candidate_id, 'DISMISS')
    return record


async def run_to_arrival(hub, service, execution, start_seq=3):
    endpoints = tuple(service._revisit_commands[i] for i in service._revisit_ends)
    assert endpoints
    for offset, point in enumerate(endpoints):
        await hub.publish(sample(start_seq + offset, point.latitude_deg,
                                 point.longitude_deg, point.altitude_m))
        if offset == len(endpoints) - 1:
            await wait_until(lambda: bool(service._vehicle.viewpoint_heading_calls))
            assert execution.flight_activity == FlightActivity.REVISITING
            # Reaching the XY must not report arrival until yaw is restored.
            aligned = replace(sample(start_seq + offset + 1, point.latitude_deg,
                                     point.longitude_deg, point.altitude_m),
                              heading_deg=math.degrees(service._revisit_yaw_rad) % 360)
            await hub.publish(aligned)
            await wait_until(lambda: execution.flight_activity == FlightActivity.HOLDING)
        else:
            index = service._revisit_ends[offset]
            await wait_until(lambda: service._revisit_index > index)
    return endpoints[-1]


@pytest.mark.asyncio
async def test_viewpoint_heading_restores_wraparound_before_arrival_hold():
    grid, hub, vehicle, service, _, execution = await create_running()
    try:
        store = CandidateStore('s')
        service._candidate_store = store
        # Original observation near north (359 deg). A final telemetry heading
        # near 0 deg is within tolerance even across the wrap-around.
        record = candidate(store, execution.execution_id, grid,
                           yaw=math.radians(359.0))
        await service.revisit(execution.execution_id, record.candidate_id)
        endpoints = tuple(service._revisit_commands[i] for i in service._revisit_ends)
        for idx, point in enumerate(endpoints):
            await hub.publish(sample(idx + 3, point.latitude_deg, point.longitude_deg,
                                     point.altitude_m))
            if idx < len(endpoints) - 1:
                await wait_until(lambda: service._revisit_index > service._revisit_ends[idx])
        await wait_until(lambda: len(vehicle.viewpoint_heading_calls) == 1)
        assert execution.flight_activity == FlightActivity.REVISITING
        assert vehicle.hold_calls == 1  # takeover brake only, no arrival yet
        target, yaw = vehicle.viewpoint_heading_calls[0]
        assert target == endpoints[-1]
        assert yaw == pytest.approx(math.radians(359.0))
        await hub.publish(replace(sample(10, target.latitude_deg, target.longitude_deg,
                                         target.altitude_m), heading_deg=1.0))
        await wait_until(lambda: execution.hold_reason == HoldReason.REVISIT_ARRIVAL)
        assert vehicle.flight_mode == 'BRAKE'
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_manual_takeover_while_turning_clears_heading_target():
    grid, hub, vehicle, service, _, execution = await create_running()
    try:
        store = CandidateStore('s')
        service._candidate_store = store
        record = candidate(store, execution.execution_id, grid)
        await service.revisit(execution.execution_id, record.candidate_id)
        endpoints = tuple(service._revisit_commands[i] for i in service._revisit_ends)
        for idx, point in enumerate(endpoints):
            await hub.publish(sample(idx + 3, point.latitude_deg, point.longitude_deg,
                                     point.altitude_m))
            if idx < len(endpoints) - 1:
                await wait_until(lambda: service._revisit_index > service._revisit_ends[idx])
        await wait_until(lambda: bool(vehicle.viewpoint_heading_calls))
        assert execution.flight_activity == FlightActivity.REVISITING
        await service.take_controls(execution.execution_id)
        assert execution.flight_activity == FlightActivity.MANUAL_CONTROL
        assert service._revisit_yaw_rad is None
        assert service._revisit_yaw_requested_at_s is None
        assert service._route_departure_anchor is not None
        await hub.publish(replace(sample(30, endpoints[-1].latitude_deg,
                                         endpoints[-1].longitude_deg, endpoints[-1].altitude_m),
                                  heading_deg=63.0))
        await asyncio.sleep(0.02)
        assert execution.flight_activity == FlightActivity.MANUAL_CONTROL
        assert execution.hold_reason is None
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_yaw_alignment_timeout_brakes_without_claiming_arrival():
    grid, hub, vehicle, service, _, execution = await create_running()
    try:
        store = CandidateStore('s')
        service._candidate_store = store
        record = candidate(store, execution.execution_id, grid)
        await service.revisit(execution.execution_id, record.candidate_id)
        endpoints = tuple(service._revisit_commands[i] for i in service._revisit_ends)
        for idx, point in enumerate(endpoints):
            await hub.publish(sample(idx + 3, point.latitude_deg, point.longitude_deg,
                                     point.altitude_m))
            if idx < len(endpoints) - 1:
                await wait_until(lambda: service._revisit_index > service._revisit_ends[idx])
        await wait_until(lambda: bool(vehicle.viewpoint_heading_calls))
        service._revisit_yaw_requested_at_s = time.monotonic() - 21
        point = endpoints[-1]
        await hub.publish(sample(20, point.latitude_deg, point.longitude_deg,
                                 point.altitude_m))
        await wait_until(lambda: service.background_execution_error is not None)
        assert service.background_execution_error.details['reason'] == 'REVISIT_HEADING_TIMEOUT'
        assert vehicle.flight_mode == 'BRAKE'
        assert execution.hold_reason != HoldReason.REVISIT_ARRIVAL
        assert service._revisit_yaw_rad is None
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_stop_during_final_viewpoint_turn_retains_rtl_priority():
    grid, hub, vehicle, service, _, execution = await create_running()
    try:
        store = CandidateStore('s')
        service._candidate_store = store
        record = candidate(store, execution.execution_id, grid)
        await service.revisit(execution.execution_id, record.candidate_id)
        endpoints = tuple(service._revisit_commands[i] for i in service._revisit_ends)
        for idx, point in enumerate(endpoints):
            await hub.publish(sample(idx + 3, point.latitude_deg, point.longitude_deg,
                                     point.altitude_m))
            if idx < len(endpoints) - 1:
                await wait_until(lambda: service._revisit_index > service._revisit_ends[idx])
        await wait_until(lambda: bool(vehicle.viewpoint_heading_calls))
        assert execution.flight_activity == FlightActivity.REVISITING
        await service.stop(execution.execution_id)
        assert vehicle.flight_mode == 'RTL'
        assert execution.state == MissionState.RETURNING_HOME
        assert service._revisit_yaw_rad is None
        before = len(vehicle.viewpoint_heading_calls)
        await hub.publish(sample(20, endpoints[-1].latitude_deg,
                                 endpoints[-1].longitude_deg, endpoints[-1].altitude_m))
        await asyncio.sleep(0.02)
        assert len(vehicle.viewpoint_heading_calls) == before
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_revisit_holds_at_capture_pose_and_resumes_unfinished_search():
    grid, hub, vehicle, service, plan, execution = await create_running()
    try:
        store = CandidateStore('s')
        service._candidate_store = store
        original_target = execution.active_target
        stored = candidate(store, execution.execution_id, grid, altitude=17.4)
        assert candidate_view(stored).revisit_available
        started = await service.revisit(execution.execution_id, stored.candidate_id)
        assert started is execution and execution.state == MissionState.PAUSED
        assert execution.flight_activity == FlightActivity.REVISITING
        assert execution.active_revisit_candidate_id == stored.candidate_id
        assert execution.active_target == original_target
        assert execution.completed_targets == []
        original_anchor = service._route_departure_anchor
        assert original_anchor is not None
        expected = service._revisit_commands[-1]
        assert expected.latitude_deg == stored.capture_viewpoint.latitude_deg
        assert expected.longitude_deg == stored.capture_viewpoint.longitude_deg
        assert expected.altitude_m >= grid.height_geo(expected.latitude_deg, expected.longitude_deg) + 18
        await run_to_arrival(hub, service, execution)
        assert execution.state == MissionState.PAUSED
        assert execution.flight_activity == FlightActivity.HOLDING
        assert execution.hold_reason == HoldReason.REVISIT_ARRIVAL
        assert execution.active_revisit_candidate_id is None
        assert not service._revisit_commands
        assert service._route_departure_anchor == original_anchor
        assert execution.completed_targets == []
        await service.resume(execution.execution_id)
        assert execution.state == MissionState.RUNNING
        assert execution.flight_activity == FlightActivity.REJOINING
        finish = service._recovery_commands[-1]
        assert (finish.latitude_deg, finish.longitude_deg) == (
            original_anchor.position.latitude_deg, original_anchor.position.longitude_deg)
        assert execution.completed_targets == []
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_cancel_revisit_brakes_without_resuming_and_preserves_anchor():
    grid, hub, vehicle, service, _, execution = await create_running()
    try:
        store = CandidateStore('s')
        service._candidate_store = store
        record = candidate(store, execution.execution_id, grid)
        await service.revisit(execution.execution_id, record.candidate_id)
        anchor = service._route_departure_anchor
        before_hold = vehicle.hold_calls
        await service.cancel_revisit(execution.execution_id)
        assert vehicle.hold_calls == before_hold + 1
        assert vehicle.flight_mode == 'BRAKE'
        assert execution.state == MissionState.PAUSED
        assert execution.flight_activity == FlightActivity.HOLDING
        assert execution.hold_reason == HoldReason.REVISIT_CANCELED
        assert execution.active_revisit_candidate_id is None
        assert service._route_departure_anchor is anchor
        assert not service._revisit_commands
        assert not execution.completed_targets
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_manual_takeover_during_revisit_cancels_destination_preserving_rejoin():
    grid, hub, vehicle, service, _, execution = await create_running()
    try:
        store = CandidateStore('s')
        service._candidate_store = store
        record = candidate(store, execution.execution_id, grid)
        await service.revisit(execution.execution_id, record.candidate_id)
        anchor = service._route_departure_anchor
        await service.take_controls(execution.execution_id)
        assert execution.flight_activity == FlightActivity.MANUAL_CONTROL
        assert service._route_departure_anchor is anchor
        assert not service._revisit_commands
        assert execution.active_revisit_candidate_id is None
        await service.give_controls(execution.execution_id)
        assert execution.flight_activity == FlightActivity.HOLDING
        await service.resume(execution.execution_id)
        assert execution.flight_activity == FlightActivity.REJOINING
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_stop_and_camera_loss_override_revisit_without_sending_more_guidance():
    for camera in (False, True):
        grid, hub, vehicle, service, _, execution = await create_running()
        try:
            store = CandidateStore('s')
            service._candidate_store = store
            record = candidate(store, execution.execution_id, grid)
            await service.revisit(execution.execution_id, record.candidate_id)
            before = len(vehicle.go_to_calls)
            if camera:
                assert await service.interrupt_due_to_camera_loss()
            else:
                await service.stop(execution.execution_id)
            assert vehicle.flight_mode == 'RTL'
            assert execution.state == MissionState.RETURNING_HOME
            assert not service._revisit_commands
            assert execution.active_revisit_candidate_id is None
            await hub.publish(sample(10, *grid.local_to_geo(15, -8), 24))
            await asyncio.sleep(.02)
            assert len(vehicle.go_to_calls) == before
        finally:
            await service.shutdown()


@pytest.mark.asyncio
async def test_revisit_rejects_dismissed_wrong_mission_missing_pose_and_invalid_route():
    grid, hub, vehicle, service, _, execution = await create_running()
    try:
        store = CandidateStore('s')
        service._candidate_store = store
        wrong = candidate(store, 'not-this-execution', grid)
        dismissed = candidate(store, execution.execution_id, grid, status='DISMISSED')
        outside = candidate(store, execution.execution_id, grid, north=130)
        no_pose = store.publish(first_observed_at=datetime.now(timezone.utc),
                                captured_at=datetime.now(timezone.utc), confidence=.7,
                                bounding_box=PixelBoundingBox(10, 10, 20, 40, 200, 150),
                                estimated_location=None, snapshot_jpeg=b'\xff\xd8z\xff\xd9',
                                execution_id=execution.execution_id)
        for record in (wrong, dismissed, outside, no_pose):
            with pytest.raises(MissionExecutionError):
                await service.revisit(execution.execution_id, record.candidate_id)
        assert execution.state == MissionState.RUNNING
        assert not service._revisit_commands
        assert vehicle.hold_calls == 0
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_another_revisit_from_viewpoint_does_not_replace_original_anchor():
    grid, hub, vehicle, service, _, execution = await create_running()
    try:
        store = CandidateStore('s')
        service._candidate_store = store
        first = candidate(store, execution.execution_id, grid, north=12, west=-12)
        second = candidate(store, execution.execution_id, grid, north=26, west=10)
        await service.revisit(execution.execution_id, first.candidate_id)
        anchor = service._route_departure_anchor
        final = await run_to_arrival(hub, service, execution)
        await hub.publish(sample(8, final.latitude_deg, final.longitude_deg, final.altitude_m))
        await service.revisit(execution.execution_id, second.candidate_id)
        assert service._route_departure_anchor == anchor
        assert execution.active_revisit_candidate_id == second.candidate_id
        assert not execution.completed_targets
    finally:
        await service.shutdown()

@pytest.mark.asyncio
async def test_candidate_publisher_freezes_selected_camera_pose_and_execution():
    from app.api.schemas.detections import candidate_event
    from app.vehicle.pose_history import AircraftPose
    from app.vision.candidate_publisher import CandidatePublisher
    from test_candidate_publication import tracked, person

    class History:
        captures = []

        def at_capture(self, ms):
            self.captures.append(ms)
            return AircraftPose(30.3 + len(self.captures) * .00001,
                                -97.5, 21.0, 0, 0, 1.1)

    class Localizer:
        history = History()

        def estimate(self, frame, box):
            return None

    store = CandidateStore('s')
    ids = []

    def active_execution(ms):
        ids.append(ms)
        return 'exec-1'

    pub = CandidatePublisher(store, localizer=Localizer(),
                             execution_at_capture=active_execution)
    for i, ms in enumerate((0, 500, 1100), start=1):
        await pub.process(tracked(i, ms, (person(),)))
    _, records = store.collection()
    assert len(records) == 1
    record = records[0]
    metadata = store.capture_metadata(record.candidate_id)
    assert metadata is not None
    assert ids == [metadata.capture_unix_ms]
    assert record.execution_id == 'exec-1'
    assert record.capture_viewpoint is not None
    assert record.capture_viewpoint.latitude_deg == pytest.approx(30.30001)
    # A missing person geolocation must be distinctly marked as an aircraft position.
    assert record.estimated_location is not None
    assert record.estimated_location.quality == 'CAMERA_POSITION_ONLY'
    view = candidate_view(record)
    assert view.revisit_available is True
    assert view.revisit_unavailable_reason is None
    update = store.subscribe()
    store.review(record.candidate_id, 'CONFIRM')
    event = candidate_event(update.get_nowait())
    assert event.data.candidate.revisit_available is True


def test_revisit_wire_contract_never_exposes_capture_lat_lon_altitude():
    from app.api.schemas.detections import candidate_view
    grid = __import__('test_terrain_navigation').terrain_grid()
    store = CandidateStore('s')
    record = candidate(store, 'exec-1', grid)
    wire = candidate_view(record).model_dump(by_alias=True, mode='json')
    assert wire['revisitAvailable'] is True
    assert wire['revisitUnavailableReason'] is None
    assert 'captureViewpoint' not in wire
    no_mission = candidate(store, None, grid)
    missing = candidate_view(no_mission).model_dump(by_alias=True)
    assert missing['revisitAvailable'] is False
    assert 'mission' in missing['revisitUnavailableReason']
    dismissed = store.review(record.candidate_id, 'DISMISS')
    assert candidate_view(dismissed).revisit_available is False


def test_revisit_http_routes_accept_only_candidate_id_and_serialize_state():
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes.missions import router
    from app.domain.models.mission_execution import MissionExecution

    class Service:
        calls = []

        async def revisit(self, execution_id, candidate_id):
            self.calls.append(('revisit', execution_id, candidate_id))
            return MissionExecution(execution_id=execution_id, plan_id='p',
                                    state=MissionState.PAUSED,
                                    flight_activity=FlightActivity.REVISITING,
                                    active_revisit_candidate_id=candidate_id)

        async def cancel_revisit(self, execution_id):
            self.calls.append(('cancel', execution_id))
            return MissionExecution(execution_id=execution_id, plan_id='p',
                                    state=MissionState.PAUSED,
                                    flight_activity=FlightActivity.HOLDING,
                                    hold_reason=HoldReason.OPERATOR_PAUSE)

    service = Service()

    @asynccontextmanager
    async def lifespan(app):
        app.state.mission_service = service
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    with TestClient(app) as client:
        r = client.post('/api/missions/e-1/revisit', json={'candidateId': 'c-9'})
        assert r.status_code == 200
        assert r.json()['flightActivity'] == 'REVISITING'
        assert r.json()['activeRevisitCandidateId'] == 'c-9'
        r = client.post('/api/missions/e-1/cancel-revisit')
        assert r.status_code == 200
        assert r.json()['flightActivity'] == 'HOLDING'
        assert r.json()['activeRevisitCandidateId'] is None
        r = client.post('/api/missions/e-1/revisit', json={'latitudeDeg': 10, 'longitudeDeg': -20})
        assert r.status_code == 422
    assert service.calls == [('revisit', 'e-1', 'c-9'), ('cancel', 'e-1')]

@pytest.mark.asyncio
async def test_revisit_guided_command_failure_leaves_confirmed_brake_hold():
    grid, hub, vehicle, service, _, execution = await create_running()
    try:
        store = CandidateStore('s')
        service._candidate_store = store
        record = candidate(store, execution.execution_id, grid)
        vehicle.fail_guided = True
        with pytest.raises(MissionExecutionError):
            await service.revisit(execution.execution_id, record.candidate_id)
        assert execution.state == MissionState.PAUSED
        assert execution.flight_activity == FlightActivity.HOLDING
        assert vehicle.flight_mode == 'BRAKE'
        assert vehicle.hold_calls >= 2
        assert not service._revisit_commands
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_cancel_revisit_must_never_override_native_rtl():
    grid, hub, vehicle, service, _, execution = await create_running()
    try:
        store = CandidateStore('s')
        service._candidate_store = store
        record = candidate(store, execution.execution_id, grid)
        await service.revisit(execution.execution_id, record.candidate_id)
        before = vehicle.hold_calls
        vehicle.flight_mode = 'RTL'
        with pytest.raises(MissionExecutionError):
            await service.cancel_revisit(execution.execution_id)
        assert vehicle.hold_calls == before
        assert vehicle.flight_mode == 'RTL'
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_mismatched_camera_source_or_fov_does_not_create_revisitable_pose():
    from app.vehicle.pose_history import AircraftPose
    from app.vision.candidate_publisher import CandidatePublisher
    from test_candidate_publication import tracked, person
    from types import SimpleNamespace

    class History:
        def at_capture(self, ms):
            return AircraftPose(30.0, -97.0, 20, 0, 0, .1)

    class Localizer:
        history = History()
        geometry = SimpleNamespace(source_id='expected', horizontal_fov_rad=1.05)
        def estimate(self, frame, box):
            return None

    store = CandidateStore('s')
    pub = CandidatePublisher(store, localizer=Localizer(),
                             execution_at_capture=lambda ms: 'exec-1')
    for i, ms in enumerate((0, 500, 1100), start=1):
        await pub.process(tracked(i, ms, (person(),), source='other-camera'))
    rec = store.collection()[1][0]
    assert rec.capture_viewpoint is None
    assert rec.estimated_location is None
    assert not candidate_view(rec).revisit_available
