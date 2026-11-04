import math
from io import BytesIO

from PIL import Image
import pytest

from app.domain.models.detection import PixelBoundingBox
from app.vehicle.pose_history import AircraftPose, PoseHistory
from app.vision.camera_frame import CapturedFrame
from app.vision.camera_geometry import load_camera_geometry
from app.vision.person_localization import PersonLocalizer, project_flat_ground, EARTH_RADIUS_M

G = load_camera_geometry()

def box(x=310, y=120, w=20, h=60):
    return PixelBoundingBox(x,y,w,h,640,360)

def pose(yaw=0, pitch=0, roll=0):
    return AircraftPose(30.2672,-97.7431,25,roll,pitch,yaw)


def north_east(location, reference):
    return (math.radians(location.latitude_deg - reference.latitude_deg) * EARTH_RADIUS_M,
            math.radians(location.longitude_deg-reference.longitude_deg)*EARTH_RADIUS_M * math.cos(math.radians(reference.latitude_deg)))


def test_camera_60_degrees_pointing_north_hits_ground_ahead():
    # Bottom-center pixel at exact frame center -> 60-degree optical ray.
    location = project_flat_ground(pose(), box(x=310, y=120, w=20, h=60), G)
    assert location is not None
    north,east = north_east(location,pose())
    assert north == pytest.approx(0.32 + (25.05 / math.tan(math.radians(60))),abs=.05)
    assert abs(east) < .05
    assert location.method == "FLAT_GROUND_APPROXIMATION"
    assert location.quality == "APPROXIMATE"


def test_yaw_90_rotates_footprint_to_east():
    origin=pose(yaw=math.pi/2)
    loc=project_flat_ground(origin,box(),G)
    north,east=north_east(loc,origin)
    assert abs(north) < .05
    assert east > 10


def test_forward_tilt_moves_ground_hit_closer_and_nose_up_moves_farther():
    a=project_flat_ground(pose(pitch=0),box(),G)
    b=project_flat_ground(pose(pitch=math.radians(-10)),box(),G)
    c=project_flat_ground(pose(pitch=math.radians(10)),box(),G)
    assert north_east(b,pose())[0] < north_east(a,pose())[0] < north_east(c,pose())[0]


def test_roll_changes_lateral_position():
    a=project_flat_ground(pose(),box(),G)
    b=project_flat_ground(pose(roll=math.radians(12)),box(),G)
    assert abs(north_east(a,pose())[1]-north_east(b,pose())[1]) > 1


def test_bottom_clipping_disables_location_and_negative_camera_ray_fails():
    assert project_flat_ground(pose(),box(x=300,y=300,w=20,h=60),G) is None
    # At 60 degrees, strong nose-up can put central camera ray above horizon.
    assert project_flat_ground(pose(pitch=math.radians(80)),box(),G) is None


def test_localizer_refuses_mismatched_source_or_stale_pose():
    out=BytesIO()
    Image.new("RGB", (640,360)).save(out,format="JPEG")
    history=PoseHistory()
    localizer=PersonLocalizer(history,G)
    frame=CapturedFrame("webots-iris",1,None,100000,640,360,1.10,out.getvalue())
    assert localizer.estimate(frame,box()) is None
    from types import SimpleNamespace
    history.add_position(SimpleNamespace(time_boot_ms=1000,lat=302672000,lon=-977431000,relative_alt=25000),at_unix_ms=100000)
    history.add_attitude(SimpleNamespace(time_boot_ms=1000,roll=0,pitch=0,yaw=0),at_unix_ms=100010)
    assert localizer.estimate(frame,box()) is not None
    frame2=CapturedFrame("different-source",1,None,100000,640,360,1.10,out.getvalue())
    assert localizer.estimate(frame2,box()) is None
