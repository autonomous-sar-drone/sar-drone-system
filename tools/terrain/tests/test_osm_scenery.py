"""Offline deterministic OSM scenery tests. Not an assertion about live Manor OSM data."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

import pytest
from pyproj import CRS, Transformer

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
import osm_scenery as s

@pytest.fixture
def grid():
    world=s.DEFAULT_WORLD.read_text(encoding='utf-8')
    return s.Grid(json.loads(s.DEFAULT_TERRAIN.read_text()),world)


def fixture_osm(grid, path, *, include=True):
    crs=CRS.from_proj4(f'+proj=aeqd +lat_0={grid.lat} +lon_0={grid.lon} +datum=WGS84 +units=m +no_defs')
    to_geo=Transformer.from_crs(crs,'EPSG:4326',always_xy=True)
    root=ET.Element('osm',attrib={'version':'0.6','generator':'offline-test-fixture'})
    ids={}
    def node(name,x,y):
        lon,lat=to_geo.transform(-y,x)
        i=str(1000+len(ids));ids[name]=i
        ET.SubElement(root,'node',id=i,lat=str(lat),lon=str(lon))
    if include:
        node('r0',-200,-30);node('r1',0,-30);node('r2',190,-30)
        node('b0',10,50);node('b1',28,50);node('b2',28,72);node('b3',10,72)
        road=ET.SubElement(root,'way',id='111111')
        for name in ('r0','r1','r2'):ET.SubElement(road,'nd',ref=ids[name])
        ET.SubElement(road,'tag',k='highway',v='track')
        building=ET.SubElement(root,'way',id='222222')
        for name in ('b0','b1','b2','b3','b0'):ET.SubElement(building,'nd',ref=ids[name])
        ET.SubElement(building,'tag',k='building',v='yes')
        ET.SubElement(building,'tag',k='height',v='7.5')
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
    return path


def test_archived_240_world_exact_match_and_local_projection(grid):
    assert grid.nx==61 and grid.ny==61
    assert grid.width==240 and grid.height==240
    assert grid.height_at(0,0)==pytest.approx(0)
    assert grid.height_at(-120,-120)==pytest.approx(1.041)
    assert s.xy_transformer(grid)(grid.lat,grid.lon)==pytest.approx((0,0),abs=1e-7)
    assert s.sha256_bytes(s.DEFAULT_WORLD.read_bytes())==json.loads(s.PROVENANCE.read_text())['world_sha256']


def test_world_mismatch_is_rejected(grid):
    text=s.DEFAULT_WORLD.read_text()
    damaged=text.replace('1.041, 0.992','1.042, 0.992',1)
    with pytest.raises(s.SceneryError,match='NOT identical'):
        s.Grid(json.loads(s.DEFAULT_TERRAIN.read_text()),damaged)


def test_road_clip_and_height_drape(grid):
    feature={'type':'road','id':'123','tags':{'highway':'service'},'points':[(-300,-12),(300,-12)]}
    points,faces=s.road_geometry(feature,grid)
    assert len(points)>0 and len(faces)>0
    for x,y,z in points:
        assert -120<=x<=120 and -120<=y<=120
        assert z==pytest.approx(grid.height_at(x,y)+s.ROAD_Z_OFFSET_M)
    for face in faces:
        area=s.signed_area([(points[i][0],points[i][1]) for i in face])
        assert area>0.01, 'Every road triangle should face upward, not disappear by backface culling'
    total=sum(s.signed_area([(points[i][0],points[i][1]) for i in f]) for f in faces)
    assert total==pytest.approx((240-3.2-0.04)*3.2,rel=0.01)


def test_polygon_concave_ear_clip_and_nonconvex_roof(grid):
    # L-shaped building should not get a triangle spanning its concavity.
    p=[(0,0),(20,0),(20,8),(9,8),(9,20),(0,20)]
    tris=s.roof_triangulate(p)
    assert len(tris)==len(p)-2
    assert sum(abs(s.signed_area([p[i] for i in t])) for t in tris)==pytest.approx(abs(s.signed_area(p)))


def test_complete_offline_import_preserves_iris_pedestrian_grid(tmp_path,grid):
    osm=fixture_osm(grid,tmp_path/'fixture.osm')
    out=tmp_path/'flight_v3.wbt';report=tmp_path/'report.json'
    meta=s.build(s.DEFAULT_WORLD,s.DEFAULT_TERRAIN,osm,out,report)
    w=out.read_text()
    original=s.DEFAULT_WORLD.read_text()
    assert meta['road_ways_rendered']==1
    assert meta['building_ways_rendered']==1
    assert 'SAR_OSM_SCENERY_BEGIN' in w
    assert 'SAR_OSM_BUILDING_222222' in w
    assert 'name "OSM building 222222"' in w
    assert 'name "3DEP terrain (home z=0)"' in w
    assert 'translation 38.93 -1.41 1.339' in w
    assert 'camera-fps' in w and 'sar_camera' in w
    assert 'boundingObject USE SAR_OSM_BUILDING_222222' in w
    assert 'height=7.5 m; source=height' in w
    assert '© OpenStreetMap contributors' in w
    start=w.index('\n# SAR_OSM_SCENERY_BEGIN')
    end=w.index('# SAR_OSM_SCENERY_END')+len('# SAR_OSM_SCENERY_END')+2
    assert w[:start]+w[end:]==original
    with pytest.raises(s.SceneryError,match='already exists'):
        s.build(s.DEFAULT_WORLD,s.DEFAULT_TERRAIN,osm,out,report)


def test_no_osm_features_no_fake_world(tmp_path,grid):
    empty=fixture_osm(grid,tmp_path/'empty.osm',include=False)
    out=tmp_path/'no_wbt.wbt'
    with pytest.raises(s.SceneryError,match='No usable mapped'):
        s.build(s.DEFAULT_WORLD,s.DEFAULT_TERRAIN,empty,out,tmp_path/'no_report.json')
    assert not out.exists()


def test_building_visual_height_and_clipping(grid):
    f={'id':'222','type':'building','points':[(100,-10),(150,-10),(150,20),(100,20),(100,-10)],'tags':{'building':'yes'}}
    result=s.building_geometry(f,grid)
    assert result is not None
    points,faces,h,source=result
    assert h==4 and 'visual proxy' in source
    assert len(points)>=6 and all(x<=120 for x,y,z in points)
    assert len(faces)>0

def test_webots_export_omits_ignored_mesh_hint_fields(grid):
    """No unsupported/ignored IndexedFaceSet hints in Webots R2025a export."""
    road={'type':'road','id':'111','tags':{'highway':'residential'},
          'points':[(-15,0),(15,0)]}
    building={'type':'building','id':'222','tags':{'building':'yes'},
              'points':[(10,10),(20,10),(20,20),(10,20),(10,10)]}
    road_node=s.road_world_node(road, grid)
    building_node=s.building_world_node(building, grid)
    assert road_node is not None
    assert building_node is not None
    for node in (road_node,building_node):
        assert 'IndexedFaceSet {' in node
        assert 'solid FALSE' not in node
        assert 'convex FALSE' not in node
    assert 'boundingObject USE SAR_OSM_BUILDING_222' in building_node
