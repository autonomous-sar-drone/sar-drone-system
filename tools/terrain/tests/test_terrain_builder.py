from __future__ import annotations

import json
from pathlib import Path
import re
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_scenario import verify
from terrain_builder import (
    TerrainBuildError, build_scenario, fetch_usgs_dem, height_local, load_config,
    raster_to_heights,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "scenarios/manor_prairie.json"
TEMPLATE = ROOT / "assets/iris_wsl_camera_template.wbt"


def synthetic_dem(path: Path, *, slope_n: float = .006, slope_w: float = .004):
    # This is not claimed to be real Texas terrain; only a deterministic math test.
    lat, lon = 30.3678, -97.5396
    n = 160
    half_deg = .004
    transform = from_bounds(lon-half_deg, lat-half_deg, lon+half_deg, lat+half_deg, n, n)
    rr, cc = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    xs, ys = rasterio.transform.xy(transform, rr, cc, offset='center')
    north_m = (np.asarray(ys) - lat) * 111319.49
    west_m = -(np.asarray(xs) - lon) * 111319.49 * np.cos(np.radians(lat))
    values = (171.0 + north_m*slope_n + west_m*slope_w).astype('float32').reshape((n,n))
    with rasterio.open(path, 'w', driver='GTiff', width=n, height=n,
                       count=1, dtype='float32', crs='EPSG:4326', transform=transform,
                       nodata=-9999.0) as ds:
        ds.write(values, 1)
    return path


def test_generated_world_and_backend_heights_are_identical(tmp_path):
    dem = synthetic_dem(tmp_path / "test_synthetic_dem.tif")
    output = build_scenario(CONFIG, TEMPLATE, tmp_path / "build", dem_path=dem, fetch=False)
    terra = json.loads((output / "terrain.json").read_text())
    manifest = json.loads((output / "scenario_manifest.json").read_text())
    world = (output / "manor_prairie.wbt").read_text()
    assert terra["format"] == "sar-terrain-grid-v1"
    assert terra["x_dimension"] == 81 and terra["y_dimension"] == 81
    assert terra["heights_home_relative_m"][40][40] == 0.0
    assert 2 < manifest["height_span_m"] < 5
    assert 'gpsCoordinateSystem "local"' in world
    assert 'basicTimeStep 2' in world
    assert 'controller "ardupilot_vehicle_controller"' in world
    assert 'rotation 0 1 0 1.04719755' in world
    assert 'translation 0 0 0.055' in world
    assert 'boundingObject USE SAR_ELEVATION_GRID' in world
    assert 'xDimension 81' in world and 'yDimension 81' in world
    height_text = re.search(r'height \[(.*?)\]', world, flags=re.S).group(1)
    values = list(map(float, height_text.split()))
    assert np.allclose(values, np.array(terra['heights_home_relative_m']).flatten())
    assert world.count('name "low-poly tree ') == 14


def test_bilinear_sampling_and_reject_outside(tmp_path):
    dem = synthetic_dem(tmp_path / 'synthetic.tif')
    cfg=load_config(CONFIG)
    heights, home, _ = raster_to_heights(cfg, dem)
    assert abs(home-171) < .01
    assert height_local(heights,cfg,0,0) == pytest.approx(0)
    assert height_local(heights,cfg,100,80) == pytest.approx(.006*100 + .004*80,abs=.03)
    with pytest.raises(TerrainBuildError, match='outside'):
        height_local(heights,cfg,200,0)


def test_invalid_or_missing_source_dem_refuses_generation(tmp_path):
    with pytest.raises(TerrainBuildError, match='No input DEM'):
        build_scenario(CONFIG,TEMPLATE,tmp_path/'build',dem_path=tmp_path/'no.tif',fetch=False)
    corrupt=tmp_path/'not_a_geotiff.tif'
    corrupt.write_text('incorrect')
    with pytest.raises(rasterio.errors.RasterioIOError):
        build_scenario(CONFIG,TEMPLATE,tmp_path/'build',dem_path=corrupt,fetch=False)


def test_usgs_download_validates_tiff_and_caches(tmp_path, monkeypatch):
    import terrain_builder as module
    dem = synthetic_dem(tmp_path/'fixture.tif')
    tiff = dem.read_bytes()
    called = []
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return tiff
    def fake_urlopen(request, timeout):
        called.append(request.full_url)
        return FakeResponse()
    monkeypatch.setattr(module, 'urlopen', fake_urlopen)
    path = tmp_path / 'source_dem.tif'
    assert fetch_usgs_dem(load_config(CONFIG),path)==path
    assert fetch_usgs_dem(load_config(CONFIG),path)==path
    assert len(called)==1
    assert 'pixelType=F32' in called[0] and 'format=tiff' in called[0]


def test_validator_blocks_mismatched_world_and_backend_data(tmp_path):
    dem=synthetic_dem(tmp_path/'fixture.tif')
    output=build_scenario(CONFIG,TEMPLATE,tmp_path/'build',dem_path=dem,fetch=False)
    assert verify(output)['status']=='NOT_FLIGHT_VALIDATED'
    world=output/'manor_prairie.wbt'
    world.write_text(world.read_text().replace('gpsCoordinateSystem "local"','gpsCoordinateSystem "WGS84"'))
    with pytest.raises(ValueError,match='hash mismatch'):
        verify(output)
