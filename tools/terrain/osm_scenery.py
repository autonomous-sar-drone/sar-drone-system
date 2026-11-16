"""Safely enrich a saved, *verified* Webots Manor terrain world with OSM scenery.

Developer/offline tool. World is an existing flight-tested .wbt, not rebuilt.
It adds graphical, terrain-draped roads and optional collision-enabled
footprint buildings from *actual* OpenStreetMap XML. No invented map features.

The resulting .wbt is a NEW file to copy to the simulator after inspection.
ArduPilot, MAVLink, mission logic, pedestrian and Iris controller are unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

try:
    from pyproj import CRS, Transformer
except ImportError:  # pragma: no cover - handled by CLI explanatory error
    CRS = None
    Transformer = None

HERE = Path(__file__).resolve().parent
REFERENCE = HERE / "reference"
DEFAULT_WORLD = REFERENCE / "manor_prairie_240_flight_v2.wbt"
DEFAULT_TERRAIN = REFERENCE / "manor_prairie_240_flight_v2_terrain.json"
PROVENANCE = REFERENCE / "provenance.json"
OSM_API_URL = "https://api.openstreetmap.org/api/0.6/map"
MARGIN_M = 20.0
ROAD_Z_OFFSET_M = 0.045
SAMPLE_SPACING_M = 1.5
# Source tags seldom include mapped pavement width. These are visual estimates.
ROAD_WIDTH_M = {
    "motorway": 12.0, "trunk": 10.0, "primary": 8.0,
    "secondary": 7.0, "tertiary": 6.0, "unclassified": 5.0,
    "residential": 5.0, "service": 3.2, "living_street": 4.0,
    "track": 2.7, "path": 1.4, "footway": 1.4,
    "cycleway": 1.6, "bridleway": 1.8, "pedestrian": 2.5,
}


class SceneryError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fmt(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") if value else "0"


def world_terrain_grid(world: str) -> tuple[list[float], int, int, float, float]:
    match = re.search(r"\bDEF\s+SAR_ELEVATION_GRID\s+ElevationGrid\s*\{\s*height\s*\[([^\]]*)\]"
                      r"\s*xDimension\s+(\d+)\s*xSpacing\s+([\d.]+)"
                      r"\s*yDimension\s+(\d+)\s*ySpacing\s+([\d.]+)", world, re.S)
    if not match:
        raise SceneryError("Unrecognized final Manor elevation grid; refusing to edit world")
    floats = [float(a) for a in match.group(1).replace(",", " ").split()]
    xdim, xspace, ydim, yspace = int(match.group(2)), float(match.group(3)), int(match.group(4)), float(match.group(5))
    if len(floats) != xdim * ydim:
        raise SceneryError("ElevationGrid point count mismatch")
    return floats, xdim, ydim, xspace, yspace


class Grid:
    def __init__(self, data: dict, world: str):
        if data.get("format") != "sar-terrain-grid-v1":
            raise SceneryError("Unexpected terrain schema")
        if not data.get("world_x_is_north") or not data.get("world_y_is_west"):
            raise SceneryError("Geo-to-local convention mismatch")
        self.lat = float(data["origin_latitude_deg"])
        self.lon = float(data["origin_longitude_deg"])
        self.width = float(data["width_m"])
        self.height = float(data["height_m"])
        self.spacing = float(data["grid_spacing_m"])
        self.heights = data["heights_home_relative_m"]
        self.nx = int(data["x_dimension"])
        self.ny = int(data["y_dimension"])
        if len(self.heights) != self.ny or any(len(row) != self.nx for row in self.heights):
            raise SceneryError("Non-rectangular terrain grid")
        values, nx, ny, sx, sy = world_terrain_grid(world)
        if (nx, ny) != (self.nx, self.ny) or abs(sx-self.spacing) > 1e-8 or abs(sy-self.spacing) > 1e-8:
            raise SceneryError("Webots elevation grid differs from backend terrain grid")
        if abs((nx - 1)*sx-self.width) > 1e-7 or abs((ny-1)*sy-self.height) > 1e-7:
            raise SceneryError("World extent doesn't match terrain JSON")
        if values != [float(z) for row in self.heights for z in row]:
            raise SceneryError("Terrain heights are NOT identical to saved Webots world; refusing export")
        if abs(self.height_at(0, 0)) > 0.015:
            raise SceneryError("Expected launch terrain height near zero")

    def height_at(self, x: float, y: float) -> float:
        i = (x + self.width / 2) / self.spacing
        j = (y + self.height / 2) / self.spacing
        if not (-1e-8 <= i <= self.nx-1+1e-8 and -1e-8 <= j <= self.ny-1+1e-8):
            raise SceneryError(f"Point outside saved world: {(x,y)}")
        i = min(max(0, i), self.nx-1)
        j = min(max(0, j), self.ny-1)
        i0, j0 = min(int(i), self.nx-2), min(int(j), self.ny-2)
        a, b = i-i0, j-j0
        h=self.heights
        return ((1-a)*(1-b)*h[j0][i0] + a*(1-b)*h[j0][i0+1]
                + (1-a)*b*h[j0+1][i0] + a*b*h[j0+1][i0+1])


def xy_transformer(grid: Grid):
    if CRS is None or Transformer is None:
        raise SceneryError("pyproj is required: install tools/terrain/requirements.txt")
    crs = CRS.from_proj4(f"+proj=aeqd +lat_0={grid.lat} +lon_0={grid.lon} +datum=WGS84 +units=m +no_defs")
    to_local = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    def convert(lat: float, lon: float) -> tuple[float, float]:
        east, north = to_local.transform(lon, lat)
        return float(north), float(-east)  # X=north, Y=west, Z=up
    return convert


def buffered_bbox(grid: Grid) -> tuple[float, float, float, float]:
    crs = CRS.from_proj4(f"+proj=aeqd +lat_0={grid.lat} +lon_0={grid.lon} +datum=WGS84 +units=m +no_defs")
    to_geo = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    m = MARGIN_M
    points = [to_geo.transform(-west, north)
              for north in (-grid.width/2-m, grid.width/2+m)
              for west in (-grid.height/2-m, grid.height/2+m)]
    return (min(lat for lon,lat in points), min(lon for lon,lat in points),
            max(lat for lon,lat in points), max(lon for lon,lat in points))


def download_osm(grid: Grid, cache: Path) -> str:
    if cache.exists():
        return "cached"
    south,west,north,east = buffered_bbox(grid)
    url = OSM_API_URL + "?" + urlencode({"bbox": f"{west:.8f},{south:.8f},{east:.8f},{north:.8f}"})
    req = Request(url, headers={"User-Agent": "Academic-SAR-Terrain-Prototype/0.1 (static OSM extract)", "Accept": "application/xml"})
    try:
        with urlopen(req, timeout=50) as resp:
            if resp.status != 200:
                raise SceneryError(f"OSM request returned {resp.status}")
            raw = resp.read(6_000_001)
    except Exception as exc:
        raise SceneryError(f"OSM download failed; you may provide a saved .osm file with --osm: {exc}") from exc
    if len(raw) > 6_000_000:
        raise SceneryError("OSM response unexpectedly large; refusing to cache")
    if not raw.lstrip().startswith(b"<?xml") and not raw.lstrip().startswith(b"<osm"):
        raise SceneryError("OSM service did not return XML")
    try:
        root=ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SceneryError("OSM service did not return parseable XML") from exc
    if root.tag != "osm":
        raise SceneryError("OSM response root is not <osm>")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(raw)
    return url


def osm_features(path: Path, grid: Grid) -> list[dict]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise SceneryError(f"Cannot parse OSM XML: {exc}") from exc
    if root.tag != "osm":
        raise SceneryError("OSM input must have <osm> root")
    nodes={}
    for node in root.findall('node'):
        try:
            nodes[node.attrib['id']] = (float(node.attrib['lat']),float(node.attrib['lon']))
        except (ValueError, KeyError):
            continue
    to_xy=xy_transformer(grid)
    features=[]
    for way in root.findall('way'):
        tags={t.get('k'):t.get('v') for t in way.findall('tag') if t.get('k') and t.get('v')}
        typ = 'road' if tags.get('highway') in ROAD_WIDTH_M else 'building' if tags.get('building') not in (None, 'no') else None
        if tags.get('building') not in (None, 'no'):
            typ='building'  # building+access driveway tags resolve to building
        if typ is None:
            continue
        refs=[n.get('ref') for n in way.findall('nd')]
        if len(refs)<2 or any(r not in nodes for r in refs):
            continue
        points=[to_xy(*nodes[ref]) for ref in refs]
        features.append({'type':typ,'id':str(way.get('id','unknown')),'points':points,'tags':tags})
    return features


def clip_segment(a, b, lo_x,hi_x,lo_y,hi_y):
    """Liang–Barsky line clipping against rectangle (XY)."""
    dx,dy=b[0]-a[0],b[1]-a[1]
    p=(-dx,dx,-dy,dy)
    q=(a[0]-lo_x,hi_x-a[0],a[1]-lo_y,hi_y-a[1])
    t0,t1=0.0,1.0
    for pi,qi in zip(p,q):
        if abs(pi)<1e-12:
            if qi<0: return None
            continue
        r=qi/pi
        if pi<0: t0=max(t0,r)
        else: t1=min(t1,r)
        if t0>t1: return None
    return ((a[0]+t0*dx,a[1]+t0*dy),(a[0]+t1*dx,a[1]+t1*dy))


def signed_area(points):
    return .5*sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(points,points[1:]+points[:1]))


def inside(p, axis, threshold, upper):
    return p[axis] <= threshold+1e-8 if upper else p[axis]>=threshold-1e-8


def polygon_clip(points, bounds):
    lo_x,hi_x,lo_y,hi_y=bounds
    poly=list(points)
    for axis,threshold,upper in ((0,lo_x,False),(0,hi_x,True),(1,lo_y,False),(1,hi_y,True)):
        nxt=[]
        for a,b in zip(poly[-1:]+poly[:-1],poly):
            ina,inb=inside(a,axis,threshold,upper),inside(b,axis,threshold,upper)
            if ina != inb:
                delta=b[axis]-a[axis]
                if abs(delta)>1e-12:
                    t=(threshold-a[axis])/delta
                    nxt.append((a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])))
            if inb:nxt.append(b)
        poly=nxt
        if len(poly)<3: return []
    result=[]
    for p in poly:
        if not result or math.dist(p,result[-1])>1e-6:
            result.append(p)
    if len(result)>2 and math.dist(result[0],result[-1])<1e-6:result.pop()
    return result if len(result)>=3 and abs(signed_area(result))>0.01 else []


def tri_ccw(tri, points):
    a,b,c=(points[i] for i in tri)
    return tuple(tri) if signed_area([a,b,c])>0 else (tri[0],tri[2],tri[1])


def mesh_vrml(points3d, faces):
    pts='\n'.join('          '+ ' '.join(fmt(v) for v in xyz) for xyz in points3d)
    idx='\n'.join('          '+' '.join(str(v) for v in face)+' -1' for face in faces)
    return 'IndexedFaceSet {\n      coord Coordinate {\n        point [\n'+pts+'\n        ]\n      }\n      coordIndex [\n'+idx+'\n      ]\n      creaseAngle 0.5\n    }'


def road_width(tags):
    raw=tags.get('width', '')
    m=re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:m|meters?)?\s*",raw,flags=re.I)
    if m:
        w=float(m.group(1))
        if 0.5<=w<=25:return w
    return ROAD_WIDTH_M[tags['highway']]


def road_geometry(feature,grid:Grid):
    tags=feature['tags']
    width=road_width(tags)
    half=width/2
    bounds=(-grid.width/2+half+0.02,grid.width/2-half-0.02,-grid.height/2+half+0.02,grid.height/2-half-0.02)
    pts=[]; faces=[]
    for a,b in zip(feature['points'],feature['points'][1:]):
        segment=clip_segment(a,b,*bounds)
        if segment is None:continue
        a,b=segment
        dx,dy=b[0]-a[0],b[1]-a[1]
        dist=math.hypot(dx,dy)
        if dist<1e-5:continue
        px,py=-dy/dist*half,dx/dist*half
        segments=max(1,math.ceil(dist/SAMPLE_SPACING_M))
        for i in range(segments):
            ends=[]
            for j in (i,i+1):
                t=j/segments
                x,y=a[0]+t*dx,a[1]+t*dy
                ends.extend([(x+px,y+py),(x-px,y-py)])
            offset=len(pts)
            pts.extend([(x,y,grid.height_at(x,y)+ROAD_Z_OFFSET_M) for x,y in ends])
            # Triangles use +Z winding in the XY plane
            if signed_area(ends[:3])>0:
                faces.extend([(offset,offset+1,offset+2),(offset+1,offset+3,offset+2)])
            else:
                faces.extend([(offset,offset+2,offset+1),(offset+1,offset+2,offset+3)])
    return pts,faces


def roof_triangulate(poly):
    """Ear-clip simple polygons; reject unsupported non-simple footprints."""
    if len(poly)<3:return []
    idx=list(range(len(poly)))
    if signed_area(poly)<0:idx.reverse()
    out=[]
    for _ in range(len(poly)*len(poly)):
        if len(idx)==3:
            out.append(tuple(idx));return out
        found=False
        for k in range(len(idx)):
            ia,ib,ic=idx[k-1],idx[k],idx[(k+1)%len(idx)]
            a,b,c=poly[ia],poly[ib],poly[ic]
            if signed_area([a,b,c]) < 1e-8:continue
            def in_tri(p):
                return all(signed_area([poly[s],poly[t],p])>=-1e-8 for s,t in ((ia,ib),(ib,ic),(ic,ia)))
            if any(in_tri(poly[i]) for i in idx if i not in (ia,ib,ic)):
                continue
            out.append((ia,ib,ic))
            idx.pop(k)
            found=True
            break
        if not found:return []
    return []


def building_height(tags):
    raw=str(tags.get('height','')).strip()
    found=re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:m|meters?)?",raw,flags=re.I)
    if found:
        meters=float(found.group(1))
        if 2<=meters<=80:return meters,'height'
    found=re.fullmatch(r"(\d+(?:\.\d+)?)",str(tags.get('building:levels','')).strip())
    if found:
        levels=float(found.group(1))
        if 1<=levels<=25:return levels*3.0,'building:levels (3 m/level estimate)'
    return 4.0,'unmapped (4 m visual proxy)'


def building_geometry(feature, grid):
    raw=feature['points']
    if len(raw)<4 or math.dist(raw[0],raw[-1])>0.05:
        return None
    poly=polygon_clip(raw[:-1],(-grid.width/2+0.1,grid.width/2-0.1,-grid.height/2+0.1,grid.height/2-0.1))
    if len(poly)<3 or abs(signed_area(poly))<4:
        return None
    if signed_area(poly)<0:poly.reverse()
    roof_tri=roof_triangulate(poly)
    if len(roof_tri)!=len(poly)-2:
        return None
    height,source=building_height(feature['tags'])
    elevations=[grid.height_at(x,y) for x,y in poly]
    low=min(elevations)-0.35  # sink bottom below ground rather than leave a gap on slopes
    top=max(elevations)+height
    n=len(poly)
    pts=[(x,y,low) for x,y in poly]+[(x,y,top) for x,y in poly]
    faces=[]
    for a,b,c in roof_tri:
        faces.extend([(n+a,n+b,n+c),(c,b,a)])
    for i in range(n):
        j=(i+1)%n
        faces.extend([(i,j,n+j),(i,n+j,n+i)])
    return pts,faces,height,source


def road_world_node(feature,grid):
    pts,faces=road_geometry(feature,grid)
    if not faces:return None
    label=feature['tags']['highway']
    color=(0.29,0.28,0.26) if label in {'motorway','trunk','primary','secondary','tertiary','residential','service','living_street','unclassified'} else (0.49,0.43,0.34)
    rgb=' '.join(fmt(v) for v in color)
    return (f'# OSM highway way {feature["id"]}; class={label}; approximate surface, visual only\n'
            'Shape {\n  appearance PBRAppearance { baseColor '+rgb+' roughness 1 }\n'
            '  geometry '+mesh_vrml(pts,faces)+'\n}\n')


def building_world_node(feature,grid):
    r=building_geometry(feature,grid)
    if r is None:return None
    pts,faces,height,source=r
    name=f'SAR_OSM_BUILDING_{feature["id"]}'
    if not re.fullmatch(r'SAR_OSM_BUILDING_\d+',name):
        return None
    geom=mesh_vrml(pts,faces)
    # Webots supports triangulated IndexedFaceSet in a Solid boundingObject.
    return (f'# OSM building way {feature["id"]}; height={fmt(height)} m; source={source}\n'
            'Solid {\n  children [\n    Shape {\n'
            '      appearance PBRAppearance { baseColor 0.63 0.58 0.49 roughness 1 }\n'
            f'      geometry DEF {name} {geom}\n'
            '    }\n  ]\n'
            f'  name "OSM building {feature["id"]}"\n'
            f'  boundingObject USE {name}\n  locked TRUE\n}}\n')


def build(world_path:Path, terrain_path:Path,osm_path:Path,output:Path,report_path:Path,
          *,overwrite:bool=False):
    if output.resolve()==world_path.resolve() or output.resolve()==DEFAULT_WORLD.resolve():
        raise SceneryError("Output MUST be different from original flight v2 world")
    if output.exists() and not overwrite:
        raise SceneryError(f"Output already exists: {output}; use --overwrite only after backing it up")
    if report_path.exists() and not overwrite:
        raise SceneryError(f"Report already exists: {report_path}")
    source=world_path.read_bytes()
    world=source.decode('utf-8')
    provenance=json.loads(PROVENANCE.read_text('utf-8'))
    if sha256_bytes(source)!=provenance['world_sha256']:
        raise SceneryError("Original world bytes differ from archived v2; refusing unexpected changes")
    if 'SAR_OSM_SCENERY_BEGIN' in world:
        raise SceneryError("World already contains OSM scenery marker; refusing double import")
    grid=Grid(json.loads(terrain_path.read_text('utf-8')),world)
    features=osm_features(osm_path,grid)
    nodes=[];road_count=0;building_count=0;skipped=[]
    for item in features:
        node=road_world_node(item,grid) if item['type']=='road' else building_world_node(item,grid)
        if node:
            nodes.append(node)
            if item['type']=='road':road_count+=1
            else:building_count+=1
        else:
            skipped.append({'type':item['type'],'osm_id':item['id'],'reason':'outside bounds, incomplete or unsupported geometry'})
    # Don't silently invent roads and buildings or write an unchanged "improvement".
    if not nodes:
        raise SceneryError("No usable mapped highway/building geometry intersects this 240 m world. No world created. Consider a different verified geographic area or user-approved synthetic scenery.")
    match=re.search(r'(?m)^Iris \{',world)
    if match is None or world.count('\nIris {')!=1:
        raise SceneryError("Cannot find unique top-level Iris controller block")
    annotation=("\n# SAR_OSM_SCENERY_BEGIN\n"
                "# Map data: © OpenStreetMap contributors, Open Database License (ODbL).\n"
                "# Source: https://www.openstreetmap.org/copyright\n"
                "# Roads are visual only; buildings use static collision meshes.\n"
                + '\n'.join(nodes)+"# SAR_OSM_SCENERY_END\n\n")
    new_world=world[:match.start()]+annotation+world[match.start():]
    # Strictly preserve Iris and all original content on either side of insertion.
    if new_world.replace(annotation,'',1)!=world:
        raise SceneryError('Internal source-preservation validation failed')
    output.parent.mkdir(parents=True,exist_ok=True)
    report_path.parent.mkdir(parents=True,exist_ok=True)
    # Writes are after validation, unlike mutable live-scene changes.
    output.write_text(new_world,encoding='utf-8')
    report={
        'status':'GENERATED_NOT_FLIGHT_VALIDATED',
        'generated_utc':datetime.now(timezone.utc).isoformat(),
        'source_world':str(world_path),
        'source_world_sha256':sha256_bytes(source),
        'output_world':str(output),
        'output_world_sha256':sha256_bytes(new_world.encode('utf-8')),
        'terrain_file':str(terrain_path),
        'osm_file':str(osm_path),
        'osm_file_sha256':sha256_bytes(osm_path.read_bytes()),
        'osm_license':'© OpenStreetMap contributors; Open Database License (ODbL)',
        'world_origin_lat_lon':[grid.lat,grid.lon],
        'world_size_m':[grid.width,grid.height],
        'road_ways_rendered':road_count,
        'building_ways_rendered':building_count,
        'building_height_policy':'height tag, else 3 m per building:levels, else explicitly estimated 4 m visual proxy',
        'skipped_ways':skipped,
        'warnings':['Webots loading, collision, person detection and flight NOT tested',
                    'Roads are appearance overlays, not cleared obstacle corridors',
                    'OSM mapping may be incomplete; unmapped features were not fabricated',
                    'Buildings are NOT represented in bare-earth terrain.json or autonomous clearance'],
    }
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--world',type=Path,default=DEFAULT_WORLD)
    p.add_argument('--terrain',type=Path,default=DEFAULT_TERRAIN)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--osm',type=Path,help='Already downloaded OSM XML (no network)')
    source.add_argument('--fetch-osm',action='store_true',help='Download one small OSM extract (cache on disk)')
    p.add_argument('--osm-cache',type=Path,default=HERE/'generated'/'manor_prairie_240'/'manor_prairie_240.osm')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--overwrite',action='store_true',help='Explicitly overwrite existing v3 export + report, never original v2')
    args=p.parse_args(argv)
    try:
        original=args.world.read_bytes()
        world=original.decode('utf-8')
        provenance=json.loads(PROVENANCE.read_text())
        if sha256_bytes(original)!=provenance['world_sha256']:
            raise SceneryError('Original world differs from archived reference')
        grid=Grid(json.loads(args.terrain.read_text()),world)
        osm=args.osm if args.osm else args.osm_cache
        if args.fetch_osm:
            download_osm(grid,osm)
        report=build(args.world,args.terrain,osm,args.output,args.report,overwrite=args.overwrite)
    except (SceneryError,FileNotFoundError,UnicodeDecodeError,PermissionError) as exc:
        print('ERROR:',exc,file=sys.stderr)
        return 2
    print(json.dumps({k:report[k] for k in ('status','output_world','road_ways_rendered','building_ways_rendered','skipped_ways')},indent=2))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
