# Manor terrain tools

Python 3 is required.

Install dependencies:
python -m pip install -r tools/terrain/requirements.txt pytest

Run tests:
python -m pytest -q tools/terrain/tests

Generate the Manor world:
python tools/terrain/osm_scenery.py --fetch-osm --output tools/terrain/generated/manor_prairie_240/manor_prairie_240_roads_buildings_v4.wbt

The generator uses the saved V2 world and matching terrain grid as its starting point.
The validated V4 world is stored separately in tools/terrain/worlds.

OSM data: OpenStreetMap contributors, ODbL.
