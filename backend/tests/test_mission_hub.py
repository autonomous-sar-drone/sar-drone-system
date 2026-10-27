from app.domain.models.mission_execution import MissionExecution
from app.domain.models.mission_state import MissionState
from app.domain.models.route import AltitudeReference, Waypoint
from app.runtime.mission_hub import MissionHub


def make_execution() -> MissionExecution:
    return MissionExecution(
        execution_id="execution-a", plan_id="plan-a",
        state=MissionState.PREFLIGHT_CHECK,
    )


def test_events_are_snapshots_and_include_target_changes() -> None:
    hub = MissionHub()
    queue, initial = hub.subscribe()
    assert initial is None

    execution = make_execution()
    hub.publish(execution)
    execution.state = MissionState.RUNNING
    execution.active_target = Waypoint(
        latitude_deg=30.2674,
        longitude_deg=-97.7430,
        altitude_m=25.0,
        altitude_reference=AltitudeReference.HOME_RELATIVE,
    )
    hub.publish(execution)

    # the queued preflight event should not change when the execution mutates
    first = queue.get_nowait()
    second = queue.get_nowait()

    assert first.state == MissionState.PREFLIGHT_CHECK
    assert first.active_target is None
    assert second.state == MissionState.RUNNING
    assert second.active_target is not None
    assert second.active_target.altitude_reference == AltitudeReference.HOME_RELATIVE
    assert second.timestamp_utc.tzinfo is not None

    execution.state = MissionState.PAUSED
    assert second.state == MissionState.RUNNING

    # new subscribers get the last snapshot without replaying the whole queue
    reconnect_queue, latest = hub.subscribe()
    assert latest is not None
    assert latest.state == MissionState.RUNNING
    hub.unsubscribe(queue)
    hub.unsubscribe(reconnect_queue)


def test_runtime_error_is_copied_in_snapshots_and_available_on_reconnect() -> None:
    from app.mission.errors import MissionExecutionError, MissionExecutionErrorCode
    from app.api.schemas.mission_execution import mission_update_event_from_snapshot

    hub = MissionHub()
    execution = make_execution()
    hub.publish(execution)
    details = {"reason": "UNEXPECTED_DISARM"}
    error = MissionExecutionError(
        code=MissionExecutionErrorCode.VEHICLE_NOT_READY,
        message="Guidance stopped after disarm.",
        details=details,
    )
    hub.publish(execution, runtime_error=error)
    # changing the original dictionary should not rewrite a published error
    details["reason"] = "CHANGED_LATER"

    queue, latest = hub.subscribe()
    assert latest is not None and latest.runtime_error is not None
    assert latest.runtime_error.details == {"reason": "UNEXPECTED_DISARM"}
    serialized = mission_update_event_from_snapshot(latest).model_dump(
        mode="json", by_alias=True, exclude_none=True,
    )
    assert serialized["data"]["runtimeError"] == {
        "code": "VEHICLE_NOT_READY", "message": "Guidance stopped after disarm.",
        "details": {"reason": "UNEXPECTED_DISARM"},
    }
    hub.unsubscribe(queue)
