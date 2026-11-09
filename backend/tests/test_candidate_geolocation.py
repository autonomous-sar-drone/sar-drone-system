from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from app.vision.camera_frame import CapturedFrame
from app.vision.camera_geometry import load_camera_geometry
from app.vision.person_localization import PersonLocalizer
from app.vision.person_detector import PersonBox, TrackedFrame
from app.vision.candidate_publisher import CandidatePublisher
from app.vision.candidate_store import CandidateStore
from app.vehicle.pose_history import PoseHistory


def frame(i,ms):
    output=BytesIO()
    Image.new("RGB",(640,360),(122,111,90)).save(output,format="JPEG")
    return TrackedFrame(CapturedFrame("webots-iris",i,None,ms,640,360,1.10,output.getvalue()),(
        PersonBox(310,120,330,180,0.85,1),
    ))


@pytest.mark.asyncio
async def test_candidate_uses_capture_time_pose_not_publication_time():
    history=PoseHistory()
    store=CandidateStore("test")
    person=CandidatePublisher(store,localizer=PersonLocalizer(history,load_camera_geometry()))
    ms=1793700000000
    history.add_position(SimpleNamespace(time_boot_ms=10000,lat=302672000,lon=-977431000,relative_alt=25000),at_unix_ms=ms)
    history.add_attitude(SimpleNamespace(time_boot_ms=10000,roll=0,pitch=0,yaw=0),at_unix_ms=ms)
    for i,dt in enumerate((0,600,1100),start=1):
        await person.process(frame(i,ms+dt))
    candidates=store.collection()[1]
    assert len(candidates)==1
    assert candidates[0].estimated_location is not None
    assert candidates[0].estimated_location.quality=="APPROXIMATE"
    # Capture metadata points to selected evidence, never the later publication.
    assert store.capture_metadata(candidates[0].candidate_id).capture_unix_ms==ms


@pytest.mark.asyncio
async def test_candidate_without_pose_still_publishes_with_null_location():
    history=PoseHistory()
    store=CandidateStore("test")
    person=CandidatePublisher(store,localizer=PersonLocalizer(history,load_camera_geometry()))
    ms=1793700000000
    for i,dt in enumerate((0,600,1100),start=1):
        await person.process(frame(i,ms+dt))
    candidates=store.collection()[1]
    assert len(candidates)==1
    assert candidates[0].estimated_location is None


@pytest.mark.asyncio
async def test_location_math_error_does_not_suppress_candidate_publication():
    class BrokenLocalizer:
        def estimate(self, frame, box):
            raise ArithmeticError("bad ray")

    store = CandidateStore("test")
    publisher = CandidatePublisher(store, localizer=BrokenLocalizer())
    ms = 1793700000000
    for i, dt in enumerate((0, 600, 1100), start=1):
        await publisher.process(frame(i, ms + dt))
    assert len(store.collection()[1]) == 1
    assert store.collection()[1][0].estimated_location is None
