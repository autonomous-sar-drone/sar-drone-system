"""Generate the project camera world from the user's existing Iris world.

The original `.wbt` is never overwritten. This also preserves the currently
configured WSL SITL return address and any other untouched world values.
"""
import re
import json
import math
from pathlib import Path


CAMERA_ARGS = '    "--camera"\n    "sar_camera"\n    "--camera-fps"\n    "10"\n    "--sar-camera-host"\n    "127.0.0.1"\n    "--sar-camera-port"\n    "8766"'
# Keep the original mount geometry as the strict upgrade baseline.
# Only the configured values below should change in a generated camera world.
CAMERA_EXTENSION = '  # Camera mount is outside the Iris 0.47 m body box (x extends to +0.235 m).\n  # A camera at x=0.05 was inside the airframe and saw its own shell.\n  # Mount 0.32 m forward to clear the nose while retaining fixed geometry.\n  # Webots Camera looks along local +X. Positive Y rotation tilts it\n  # forward (+X) and downward (-Z), relative to the Iris body.\n  # Keep fixed geometry; document it for future capture-pose projection.\n  extensionSlot [\n    Camera {\n      name "sar_camera"\n      translation 0.32 0 0.05\n      rotation 0 1 0 0.70\n      width 640\n      height 360\n      fieldOfView 1.10\n      near 0.05\n    }\n  ]'

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "camera_geometry.json"
EXPECTED_KEYS = {
    "mountForwardM", "mountLeftM", "mountUpM", "pitchDownDeg",
    "horizontalFovRad", "imageWidthPx", "imageHeightPx", "sourceId",
}


def load_world_camera_geometry(path: Path = CONFIG_PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != EXPECTED_KEYS:
        raise ValueError("unexpected camera geometry configuration keys")
    if not isinstance(data["sourceId"], str) or data["sourceId"] != "webots-iris":
        raise ValueError("camera source id must match the installed Webots camera sender")
    for name in ("mountForwardM", "mountLeftM", "mountUpM", "pitchDownDeg", "horizontalFovRad"):
        value = data[name]
        if type(value) not in (float, int) or not math.isfinite(value):
            raise ValueError(f"invalid camera geometry {name}")
    if not (0.0 < data["pitchDownDeg"] <= 90.0):
        raise ValueError("camera pitch must be 0–90 degrees downward")
    if not (0.0 < data["horizontalFovRad"] < math.pi):
        raise ValueError("invalid camera FOV")
    if not (0.0 <= data["mountForwardM"] <= 2.0 and abs(data["mountLeftM"]) <= 2.0 and abs(data["mountUpM"]) <= 2.0):
        raise ValueError("camera mount translation outside supported limits")
    for name in ("imageWidthPx", "imageHeightPx"):
        if type(data[name]) is not int or not (1 <= data[name] <= 4096):
            raise ValueError(f"invalid camera geometry {name}")
    return data


def camera_extension(data: dict | None = None) -> str:
    if data is None:
        data = load_world_camera_geometry()
    # Render the fixed-mount values into the only Webots Camera declaration.
    # The original world (Iris flight settings/physics/SITL address) is untouched.
    text = CAMERA_EXTENSION
    text = text.replace("Mount 0.32 m forward", "Mount %g m forward" % data["mountForwardM"])
    text = text.replace("translation 0.32 0 0.05", "translation %g %g %g" % (
        data["mountForwardM"], data["mountLeftM"], data["mountUpM"],
    ))
    text = text.replace("rotation 0 1 0 0.70", "rotation 0 1 0 %.8f" % math.radians(data["pitchDownDeg"]))
    text = text.replace("width 640", f"width {data['imageWidthPx']}")
    text = text.replace("height 360", f"height {data['imageHeightPx']}")
    text = text.replace("fieldOfView 1.10", "fieldOfView %.8f" % data["horizontalFovRad"])
    return text


def normalize_sitl_address(world: str) -> str:
    match = re.compile(r'("--sitl-address"\s*\n\s*")([^"\n]+)(")')
    if len(match.findall(world)) != 1:
        raise ValueError("world must contain one SITL address")
    return match.sub(lambda m: m.group(1) + "SITL_ADDRESS_PLACEHOLDER" + m.group(3), world)


def make_camera_world(world: str, *, extension: str | None = None) -> str:
    if 'extensionSlot [' in world or '"--camera"' in world:
        raise ValueError("expected original Iris world without a camera")
    # Find the ArduPilot controller's known, two-line SITL return address.
    address = re.compile(r'(?P<entry>    "--sitl-address"\s*\n    "[^"\n]+")')
    if len(address.findall(world)) != 1:
        raise ValueError("cannot safely locate Iris controllerArgs")
    result = address.sub(lambda m: m.group('entry') + "\n" + CAMERA_ARGS, world)
    # In the source world, Iris is the final node. Only append to that Iris.
    final = result.rfind('\n}')
    if final < 0 or not result[final:].strip() == '}':
        raise ValueError("cannot safely locate the final Iris block")
    return result[:final] + "\n" + (extension if extension is not None else camera_extension()) + result[final:]
