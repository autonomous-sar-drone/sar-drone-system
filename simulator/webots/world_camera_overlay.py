"""Generate the project camera world from the user's existing Iris world.

The original `.wbt` is never overwritten. This also preserves the currently
configured WSL SITL return address and any other untouched world values.
"""
import re


CAMERA_ARGS = '    "--camera"\n    "sar_camera"\n    "--camera-fps"\n    "10"\n    "--sar-camera-host"\n    "127.0.0.1"\n    "--sar-camera-port"\n    "8766"'
CAMERA_EXTENSION = '  # Camera mount is outside the Iris 0.47 m body box (x extends to +0.235 m).\n  # A camera at x=0.05 was inside the airframe and saw its own shell.\n  # Mount 0.32 m forward to clear the nose while retaining fixed geometry.\n  # Webots Camera looks along local +X. Positive Y rotation tilts it\n  # forward (+X) and downward (-Z), relative to the Iris body.\n  # Keep fixed geometry; document it for future capture-pose projection.\n  extensionSlot [\n    Camera {\n      name "sar_camera"\n      translation 0.32 0 0.05\n      rotation 0 1 0 0.70\n      width 640\n      height 360\n      fieldOfView 1.10\n      near 0.05\n    }\n  ]'

def normalize_sitl_address(world: str) -> str:
    match = re.compile(r'("--sitl-address"\s*\n\s*")([^"\n]+)(")')
    if len(match.findall(world)) != 1:
        raise ValueError("world must contain one SITL address")
    return match.sub(lambda m: m.group(1) + "SITL_ADDRESS_PLACEHOLDER" + m.group(3), world)


def make_camera_world(world: str) -> str:
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
    return result[:final] + "\n" + CAMERA_EXTENSION + result[final:]
