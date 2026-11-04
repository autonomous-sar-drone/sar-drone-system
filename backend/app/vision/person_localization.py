"""Flat-ground estimate of the bottom-center of a pedestrian box.

Camera local +X points forward, +Y left, +Z up in the installed Webots Iris.
Aircraft frame is MAVLink FRD: forward, right, down. Earth frame is NED.
The 0m ground plane is at home altitude, not terrain altitude. The result is
only an APPROXIMATE candidate estimate, not a measured person GPS fix.
"""
import math
from app.domain.models.detection import EstimatedPersonLocation, PixelBoundingBox
from app.vehicle.pose_history import AircraftPose, PoseHistory
from app.vision.camera_frame import CapturedFrame
from app.vision.camera_geometry import CameraGeometry

EARTH_RADIUS_M = 6378137.0
MAX_GROUND_DISTANCE_M = 500.0


def _body_to_ned(x: float, y: float, z: float, pose: AircraftPose) -> tuple[float, float, float]:
    # Roll about forward, pitch positive nose-up, yaw clockwise from north.
    cr, sr = math.cos(pose.roll_rad), math.sin(pose.roll_rad)
    cp, sp = math.cos(pose.pitch_rad), math.sin(pose.pitch_rad)
    cy, sy = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    # Rz(yaw) Ry(pitch) Rx(roll), in NED/FRD axes.
    xx = x
    yy = cr * y - sr * z
    zz = sr * y + cr * z
    x2 = cp * xx + sp * zz
    z2 = -sp * xx + cp * zz
    return cy * x2 - sy * yy, sy * x2 + cy * yy, z2


def project_flat_ground(
    pose: AircraftPose, box: PixelBoundingBox, geometry: CameraGeometry,
) -> EstimatedPersonLocation | None:
    if (box.frame_width_px != geometry.image_width_px or
            box.frame_height_px != geometry.image_height_px):
        return None
    # If lower image boundary cuts off feet, the box bottom is not a reliable
    # ground contact point; never make up a coordinate from its center.
    if box.y_px + box.height_px >= box.frame_height_px - 1:
        return None
    w, h = box.frame_width_px, box.frame_height_px
    fx = (w / 2) / math.tan(geometry.horizontal_fov_rad / 2)
    fy = fx  # square camera pixels: FOV derives from aspect ratio
    px = (box.x_px + box.width_px / 2 - w / 2) / fx
    py = (box.y_px + box.height_px - h / 2) / fy

    angle = math.radians(geometry.pitch_down_deg)
    ca, sa = math.cos(angle), math.sin(angle)
    # In body FRD the camera optical ray is +X at zero mount pitch;
    # pixel right is +Y and pixel down is +Z.
    rx = ca - py * sa
    ry = px
    rz = sa + py * ca
    ray_n, ray_e, ray_d = _body_to_ned(rx, ry, rz, pose)
    mount_n, mount_e, mount_d = _body_to_ned(
        geometry.mount_forward_m, -geometry.mount_left_m, -geometry.mount_up_m, pose,
    )
    if ray_d <= 1e-5:
        return None
    distance_along_ray = (pose.relative_altitude_m - mount_d) / ray_d
    if distance_along_ray <= 0:
        return None
    north = mount_n + distance_along_ray * ray_n
    east = mount_e + distance_along_ray * ray_e
    if math.hypot(north, east) > MAX_GROUND_DISTANCE_M:
        return None
    lat = pose.latitude_deg + math.degrees(north / EARTH_RADIUS_M)
    cos_lat = math.cos(math.radians(pose.latitude_deg))
    if abs(cos_lat) < 1e-6:
        return None
    lon = pose.longitude_deg + math.degrees(east / (EARTH_RADIUS_M * cos_lat))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return EstimatedPersonLocation(lat, lon, "FLAT_GROUND_APPROXIMATION", "APPROXIMATE")


class PersonLocalizer:
    def __init__(self, history: PoseHistory, geometry: CameraGeometry):
        self.history = history
        self.geometry = geometry

    def estimate(self, frame: CapturedFrame, box: PixelBoundingBox) -> EstimatedPersonLocation | None:
        if frame.source_id != self.geometry.source_id:
            return None
        if (frame.horizontal_fov_rad is None or
                abs(frame.horizontal_fov_rad - self.geometry.horizontal_fov_rad) > 0.002):
            return None
        pose = self.history.at_capture(frame.capture_unix_ms)
        if pose is None:
            return None
        return project_flat_ground(pose, box, self.geometry)
